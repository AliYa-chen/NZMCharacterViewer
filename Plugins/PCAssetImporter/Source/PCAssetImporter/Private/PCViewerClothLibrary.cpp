#include "PCViewerClothLibrary.h"

#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "ChaosCloth/ChaosClothConfig.h"
#include "ClothingAsset.h"
#include "ClothingSimulationFactory.h"
#include "ClothingSimulationInstance.h"
#include "ClothingSystemRuntimeTypes.h"
#include "Components/SkeletalMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "JsonObjectConverter.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "PhysicsAssetUtils.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "Rendering/SkeletalMeshModel.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SkinnedAssetCompiler.h"
#include "SkeletalMeshTypes.h"
#include "UObject/MetaData.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

namespace PCViewerCloth
{
constexpr const TCHAR* Owner = TEXT("pc_viewer_chaos_cloth_v1");
constexpr const TCHAR* OwnerKey = TEXT("PCViewerCloth.Owner");

static FString Encode(const TSharedRef<FJsonObject>& Value)
{
    FString Text;
    FJsonSerializer::Serialize(Value, TJsonWriterFactory<>::Create(&Text));
    return Text;
}

static FString Failure(const FString& Why)
{
    const auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"), TEXT("failed"));
    Report->SetStringField(TEXT("error"), Why);
    UE_LOG(LogTemp, Error, TEXT("PCViewerCloth: %s"), *Why);
    return Encode(Report);
}

static FString ObjectPath(const FString& Package)
{
    return Package + TEXT(".") + FPackageName::GetLongPackageAssetName(Package);
}

static TSharedPtr<FJsonObject> Parse(const FString& Text)
{
    TSharedPtr<FJsonObject> Result;
    FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Result);
    return Result;
}

static TSharedPtr<FJsonObject> SourceProperties(const FString& Filename, const FString& Type, FString& Why)
{
    const FString Full = FPaths::ConvertRelativePathToFull(Filename);
    const FString Allowed = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("PCFeatureIntegration/Sources/"));
    FString Text;
    if (!FPaths::IsUnderDirectory(Full, Allowed) || !FFileHelper::LoadFileToString(Text, *Full))
    {
        Why = TEXT("Cannot read verified temporary source: ") + Filename;
        return nullptr;
    }
    const auto Record = Parse(Text);
    if (!Record || Record->GetStringField(TEXT("Type")) != Type || !Record->HasTypedField<EJson::Object>(TEXT("Properties")))
    {
        Why = TEXT("Unexpected source document type: ") + Filename;
        return nullptr;
    }
    return Record->GetObjectField(TEXT("Properties"));
}

static FVector3f Vector(const TSharedPtr<FJsonObject>& Row)
{
    return FVector3f(Row->GetNumberField(TEXT("X")), Row->GetNumberField(TEXT("Y")), Row->GetNumberField(TEXT("Z")));
}

static TArray<FTransform> ReferenceTransforms(const USkeletalMesh* Mesh)
{
    TArray<FTransform> Result = Mesh->GetRefSkeleton().GetRefBonePose();
    for (int32 Index = 0; Index < Result.Num(); ++Index)
    {
        const int32 Parent = Mesh->GetRefSkeleton().GetParentIndex(Index);
        if (Parent != INDEX_NONE) Result[Index] *= Result[Parent];
    }
    return Result;
}

static bool PhysicalMesh(const TSharedPtr<FJsonObject>& Source, const int32 NumBones, const bool RequireDynamic,
    FClothPhysicalMeshData& Mesh, FString& Why)
{
    const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Normals = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Indices = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Bones = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Distances = nullptr;
    if (!Source->TryGetArrayField(TEXT("Vertices"), Positions) || !Source->TryGetArrayField(TEXT("Normals"), Normals)
        || !Source->TryGetArrayField(TEXT("Indices"), Indices) || !Source->TryGetArrayField(TEXT("BoneData"), Bones)
        || !Source->TryGetArrayField(TEXT("MaxDistances"), Distances))
    {
        Why = TEXT("Source physical mesh is incomplete"); return false;
    }
    const int32 Count = Positions->Num();
    if (Count < 3 || Count > 2000000 || Normals->Num() != Count || Bones->Num() != Count
        || Distances->Num() != Count || Indices->Num() < 3 || Indices->Num() % 3)
    {
        Why = TEXT("Source cloth vertex/normal/skinning/pin array dimensions differ"); return false;
    }
    Mesh.Reset(Count, Indices->Num());
    Mesh.MaxBoneWeights = 0;
    Mesh.NumFixedVerts = 0;
    auto& MaxDistance = Mesh.FindOrAddWeightMap(EWeightMapTargetCommon::MaxDistance).Values;
    MaxDistance.SetNumUninitialized(Count);
    for (int32 Index = 0; Index < Count; ++Index)
    {
        Mesh.Vertices[Index] = Vector((*Positions)[Index]->AsObject());
        Mesh.Normals[Index] = Vector((*Normals)[Index]->AsObject());
        MaxDistance[Index] = (*Distances)[Index]->AsNumber();
        if (Mesh.Vertices[Index].ContainsNaN() || Mesh.Normals[Index].ContainsNaN()
            || !FMath::IsFinite(MaxDistance[Index]) || MaxDistance[Index] < 0.f)
        {
            Why = TEXT("Source cloth contains a nonfinite vertex, normal, or pin distance"); return false;
        }
        Mesh.NumFixedVerts += MaxDistance[Index] == 0.f;
        const auto Input = (*Bones)[Index]->AsObject();
        FClothVertBoneData& Bone = Mesh.BoneData[Index];
        Bone.NumInfluences = Input->GetIntegerField(TEXT("NumInfluences"));
        if (Bone.NumInfluences < 1 || Bone.NumInfluences > FClothVertBoneData::MaxTotalInfluences)
        {
            Why = TEXT("Source cloth influence count is invalid"); return false;
        }
        double Total = 0.;
        for (int32 Influence = 0; Influence < Bone.NumInfluences; ++Influence)
        {
            const FString Suffix = Influence ? FString::Printf(TEXT("[%d]"), Influence) : FString();
            const int32 BoneIndex = Input->GetIntegerField(TEXT("BoneIndices") + Suffix);
            const float Weight = Input->GetNumberField(TEXT("BoneWeights") + Suffix);
            if (BoneIndex < 0 || BoneIndex >= NumBones || !FMath::IsFinite(Weight) || Weight < 0.f)
            {
                Why = TEXT("Source cloth contains an invalid named-bone mapping"); return false;
            }
            Bone.BoneIndices[Influence] = BoneIndex;
            Bone.BoneWeights[Influence] = Weight;
            Total += Weight;
        }
        if (FMath::Abs(Total - 1.) > 0.001)
        {
            Why = TEXT("Source cloth bone weights are not normalized"); return false;
        }
        Mesh.MaxBoneWeights = FMath::Max(Mesh.MaxBoneWeights, Bone.NumInfluences);
    }
    for (int32 Index = 0; Index < Indices->Num(); ++Index)
    {
        const double Value = (*Indices)[Index]->AsNumber();
        if (Value < 0 || Value >= Count || FMath::FloorToDouble(Value) != Value)
        {
            Why = TEXT("Source cloth triangle index is invalid"); return false;
        }
        Mesh.Indices[Index] = uint32(Value);
    }
    for (const TPair<FString, EWeightMapTargetCommon>& Field : {
        TPair<FString, EWeightMapTargetCommon>(TEXT("BackstopDistances"), EWeightMapTargetCommon::BackstopDistance),
        TPair<FString, EWeightMapTargetCommon>(TEXT("BackstopRadiuses"), EWeightMapTargetCommon::BackstopRadius),
        TPair<FString, EWeightMapTargetCommon>(TEXT("AnimDriveMultipliers"), EWeightMapTargetCommon::AnimDriveStiffness)})
    {
        const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
        if (!Source->TryGetArrayField(Field.Key, Values)) continue;
        if (Values->Num() != Count) { Why = TEXT("Source cloth parameter map has the wrong size"); return false; }
        auto& Map = Mesh.FindOrAddWeightMap(Field.Value).Values;
        Map.SetNumUninitialized(Count);
        for (int32 Index = 0; Index < Count; ++Index)
        {
            Map[Index] = (*Values)[Index]->AsNumber();
            if (!FMath::IsFinite(Map[Index])) { Why = TEXT("Nonfinite source cloth parameter map"); return false; }
        }
    }
    const TArray<TSharedPtr<FJsonValue>>* Masses = nullptr;
    if (Source->TryGetArrayField(TEXT("InverseMasses"), Masses))
    {
        if (Masses->Num() != Count) { Why = TEXT("Source cloth mass array has the wrong size"); return false; }
        for (int32 Index = 0; Index < Count; ++Index)
        {
            Mesh.InverseMasses[Index] = (*Masses)[Index]->AsNumber();
            if (!FMath::IsFinite(Mesh.InverseMasses[Index]) || Mesh.InverseMasses[Index] < 0.f)
            { Why = TEXT("Invalid source cloth inverse mass"); return false; }
        }
    }
    const int32 DeclaredFixed = Source->GetIntegerField(TEXT("NumFixedVerts"));
    if (DeclaredFixed != Mesh.NumFixedVerts || (RequireDynamic && (!Mesh.NumFixedVerts || Mesh.NumFixedVerts == Count)))
    {
        Why = FString::Printf(TEXT("Source cloth pin count invalid: declared=%d imported=%d vertices=%d live=%d"),
            DeclaredFixed, Mesh.NumFixedVerts, Count, int32(RequireDynamic)); return false;
    }
    return true;
}

static bool VerifyRenderSection(const FSkelMeshSection& Section, const FClothPhysicalMeshData& Physical,
    const int32 SourceTriangles, FString& Why)
{
    if (Section.NumTriangles != SourceTriangles)
    {
        Why = TEXT("Converted render section triangle count differs from the exact source cloth section"); return false;
    }
    // The exported physical mesh is intentionally a proxy: it need not share
    // render vertices or triangle count. Source section identity and the
    // converted render topology select the section; the engine computes its
    // barycentric embedding into this complete original physical mesh.
    if (Section.SoftVertices.IsEmpty() || Physical.Vertices.IsEmpty())
    { Why = TEXT("Empty render or source simulation geometry"); return false; }
    return true;
}

static bool FitCollisions(USkeletalMesh* Mesh, const TArray<TSharedPtr<FJsonValue>>& Inputs, const double GeometryScale,
    UPhysicsAsset*& Result, FString& Why)
{
    Result = NewObject<UPhysicsAsset>(Mesh, TEXT("PA_PCViewerCloth"), RF_Transactional);
    const auto TargetReference = ReferenceTransforms(Mesh);
    FPhysAssetCreateParams Params;
    Params.GeomType = EFG_Sphyl;
    Params.MinBoneSize = 5.f;
    Params.bCreateConstraints = false;
    Params.bIncludeChildBones = false;
    Params.bDisableCollisionsByDefault = true;
    TSet<FName> AddedBones;
    for (const auto& Input : Inputs)
    {
        USkeletalMesh* ColliderMesh = LoadObject<USkeletalMesh>(nullptr, *ObjectPath(Input->AsString()));
        if (!ColliderMesh) { Why = TEXT("Missing same-outfit collision geometry"); return false; }
        UPhysicsAsset* Fitted = NewObject<UPhysicsAsset>(GetTransientPackage());
        FText Error;
        if (!FPhysicsAssetUtils::CreateFromSkeletalMesh(Fitted, ColliderMesh, Params, Error, false, false))
        {
            Why = TEXT("Collision geometry fitting failed: ") + Error.ToString(); return false;
        }
        const auto SourceReference = ReferenceTransforms(ColliderMesh);
        for (const auto& Body : Fitted->SkeletalBodySetups)
        {
            const int32 TargetBone = Mesh->GetRefSkeleton().FindBoneIndex(Body->BoneName);
            if (TargetBone == INDEX_NONE || AddedBones.Contains(Body->BoneName)) continue;
            const int32 SourceBone = ColliderMesh->GetRefSkeleton().FindBoneIndex(Body->BoneName);
            if (SourceBone == INDEX_NONE || !TargetReference[TargetBone].Equals(SourceReference[SourceBone], 0.02))
            {
                Why = TEXT("Same-outfit collision bone has a different reference basis: ") + Body->BoneName.ToString(); return false;
            }
            USkeletalBodySetup* Copy = DuplicateObject<USkeletalBodySetup>(Body, Result,
                FName(*FString::Printf(TEXT("Body_%s"), *Body->BoneName.ToString())));
            // Automatic physics fits bound the outer render geometry. Hair and
            // clothing already surround it, so those outer capsules can push
            // cloth away from its small source distance constraints. Use an
            // explicitly authored inner compatibility collider: keep the
            // fitted center/orientation, shrink both radius and axial length.
            for (FKSphylElem& Capsule : Copy->AggGeom.SphylElems)
            {
                Capsule.Radius *= GeometryScale;
                Capsule.Length *= GeometryScale;
            }
            for (FKSphereElem& Sphere : Copy->AggGeom.SphereElems) Sphere.Radius *= GeometryScale;
            Result->SkeletalBodySetups.Add(Copy);
            AddedBones.Add(Body->BoneName);
        }
    }
    Result->UpdateBodySetupIndexMap();
    Result->UpdateBoundsBodiesArray();
    if (Result->SkeletalBodySetups.IsEmpty()) { Why = TEXT("No compatible fitted cloth collision bodies"); return false; }
    return true;
}

static TSharedRef<FJsonObject> Describe(USkeletalMesh* Mesh)
{
    const auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"), TEXT("passed"));
    Report->SetStringField(TEXT("mesh"), Mesh->GetPathName());
    Report->SetStringField(TEXT("skeleton"), Mesh->GetSkeleton() ? Mesh->GetSkeleton()->GetPathName() : FString());
    Report->SetBoolField(TEXT("has_active_clothing"), Mesh->HasActiveClothingAssets());
    Report->SetStringField(TEXT("collision_geometry_scale"), Mesh->GetPackage()->GetMetaData().GetValue(Mesh, TEXT("PCViewerCloth.CollisionGeometryScale")));
    TArray<TSharedPtr<FJsonValue>> Cloths;
    int32 LiveBindings = 0;
    for (UClothingAssetBase* Base : Mesh->GetMeshClothingAssets())
    {
        UClothingAssetCommon* Asset = Cast<UClothingAssetCommon>(Base);
        if (!Asset) continue;
        const auto Row = MakeShared<FJsonObject>();
        Row->SetStringField(TEXT("name"), Asset->GetName());
        Row->SetStringField(TEXT("asset"), Asset->GetPathName());
        Row->SetBoolField(TEXT("chaos_config"), Asset->GetClothConfig<UChaosClothConfig>() != nullptr);
        Row->SetNumberField(TEXT("collision_bodies"), Asset->PhysicsAsset ? Asset->PhysicsAsset->SkeletalBodySetups.Num() : 0);
        const bool Bound = Asset->LodMap.Contains(0);
        Row->SetBoolField(TEXT("live_binding"), Bound);
        LiveBindings += Bound;
        if (!Asset->LodData.IsEmpty())
        {
            const auto& Physical = Asset->LodData[0].PhysicalMeshData;
            Row->SetNumberField(TEXT("physical_vertices"), Physical.Vertices.Num());
            Row->SetNumberField(TEXT("triangles"), Physical.Indices.Num() / 3);
            Row->SetNumberField(TEXT("pinned_vertices"), Physical.NumFixedVerts);
            Row->SetNumberField(TEXT("weight_maps"), Physical.WeightMaps.Num());
            Row->SetNumberField(TEXT("bone_names"), Asset->UsedBoneNames.Num());
        }
        Cloths.Add(MakeShared<FJsonValueObject>(Row));
    }
    Report->SetArrayField(TEXT("cloth_assets"), Cloths);
    Report->SetNumberField(TEXT("live_cloth_sections"), LiveBindings);
    Report->SetNumberField(TEXT("source_cloth_assets"), Cloths.Num());
    TArray<TSharedPtr<FJsonValue>> Sections;
    if (const FSkeletalMeshModel* Model = Mesh->GetImportedModel())
        for (int32 LodIndex = 0; LodIndex < Model->LODModels.Num(); ++LodIndex)
            for (int32 SectionIndex = 0; SectionIndex < Model->LODModels[LodIndex].Sections.Num(); ++SectionIndex)
            {
                const auto& Section = Model->LODModels[LodIndex].Sections[SectionIndex];
                const auto Row = MakeShared<FJsonObject>();
                Row->SetNumberField(TEXT("lod"), LodIndex);
                Row->SetNumberField(TEXT("section"), SectionIndex);
                Row->SetNumberField(TEXT("material_index"), Section.MaterialIndex);
                Row->SetNumberField(TEXT("cloth_asset_index"), Section.CorrespondClothAssetIndex);
                Row->SetStringField(TEXT("clothing_guid"), Section.ClothingData.AssetGuid.ToString());
                Row->SetNumberField(TEXT("mapping_vertices"), Section.ClothMappingDataLODs.IsEmpty() ? 0 : Section.ClothMappingDataLODs[0].Num());
                if (const auto* User = Model->LODModels[LodIndex].UserSectionsData.Find(Section.OriginalDataSectionIndex))
                    Row->SetStringField(TEXT("source_user_clothing_guid"), User->ClothingData.AssetGuid.ToString());
                if (const auto* Render = Mesh->GetResourceForRendering(); Render && Render->LODRenderData.IsValidIndex(LodIndex)
                    && Render->LODRenderData[LodIndex].RenderSections.IsValidIndex(SectionIndex))
                {
                    const auto& RenderSection = Render->LODRenderData[LodIndex].RenderSections[SectionIndex];
                    Row->SetBoolField(TEXT("render_has_clothing"), RenderSection.HasClothingData());
                    Row->SetNumberField(TEXT("render_mapping_vertices"), RenderSection.ClothMappingDataLODs.IsEmpty() ? 0 : RenderSection.ClothMappingDataLODs[0].Num());
                }
                Sections.Add(MakeShared<FJsonValueObject>(Row));
            }
    Report->SetArrayField(TEXT("render_sections"), Sections);
    return Report;
}
}

FString UPCViewerClothLibrary::BuildClothMesh(const FString& SourceMeshPackage,
    const FString& TargetMeshPackage, const FString& OptionsJson)
{
    using namespace PCViewerCloth;
    if (!SourceMeshPackage.StartsWith(TEXT("/Game/PCAssets/Meshes/"))
        || !TargetMeshPackage.StartsWith(TEXT("/Game/PCAssets/ViewerCloth/"))
        || !FPackageName::IsValidLongPackageName(SourceMeshPackage) || !FPackageName::IsValidLongPackageName(TargetMeshPackage))
        return Failure(TEXT("Cloth authoring package namespace is invalid"));
    const auto Options = Parse(OptionsJson);
    if (!Options || Options->GetStringField(TEXT("owner")) != Owner) return Failure(TEXT("Missing explicit cloth authoring ownership"));
    USkeletalMesh* Source = LoadObject<USkeletalMesh>(nullptr, *ObjectPath(SourceMeshPackage));
    if (!Source || !Source->GetSkeleton() || !Source->GetImportedModel()) return Failure(TEXT("Missing converted source mesh or native skeleton"));
    USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *ObjectPath(TargetMeshPackage));
    if (Mesh && Mesh->GetPackage()->GetMetaData().GetValue(Mesh, OwnerKey) != Owner)
        return Failure(TEXT("Refusing to overwrite an unowned cloth target"));
    if (!Mesh)
    {
        UPackage* Package = CreatePackage(*TargetMeshPackage);
        Mesh = DuplicateObject<USkeletalMesh>(Source, Package, *FPackageName::GetLongPackageAssetName(TargetMeshPackage));
        if (!Mesh) return Failure(TEXT("Cannot duplicate source mesh for cloth"));
        Mesh->SetFlags(RF_Public | RF_Standalone);
        FAssetRegistryModule::AssetCreated(Mesh);
    }
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh, OwnerKey, Owner);
    if (Mesh->GetSkeleton() != Source->GetSkeleton()) return Failure(TEXT("Cloth copy changed the exact animation skeleton"));
    FString Why;
    UPhysicsAsset* Physics = nullptr;
    const double GeometryScale = Options->GetNumberField(TEXT("collision_geometry_scale"));
    if (!FMath::IsFinite(GeometryScale) || GeometryScale < 0.1 || GeometryScale > 1.)
        return Failure(TEXT("Explicit compatible collision geometry scale must be in [0.1,1]"));
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh, TEXT("PCViewerCloth.CollisionGeometryScale"), *FString::Printf(TEXT("%.6f"), GeometryScale));
    if (!FitCollisions(Mesh, Options->GetArrayField(TEXT("collision_meshes")), GeometryScale, Physics, Why)) return Failure(Why);
    TSharedPtr<FJsonObject> BeforeBuild;
    {
        FScopedSkeletalMeshPostEditChange Scope(Mesh);
        for (UClothingAssetBase* Existing : Mesh->GetMeshClothingAssets())
            Existing->UnbindFromSkeletalMesh(Mesh, INDEX_NONE, INDEX_NONE);
        Mesh->GetMeshClothingAssets().Empty();
        UChaosClothSharedSimConfig* Shared = NewObject<UChaosClothSharedSimConfig>(Mesh, TEXT("PC_ChaosSharedConfig"), RF_Transactional);
        bool SharedConfigured = false;
        for (const auto& Entry : Options->GetArrayField(TEXT("cloths")))
        {
            const auto Input = Entry->AsObject();
            const bool Live = Input->GetBoolField(TEXT("bind"));
            const auto Properties = SourceProperties(Input->GetStringField(TEXT("clothing_json")), TEXT("ClothingAssetNv"), Why);
            const auto Physical = SourceProperties(Input->GetStringField(TEXT("physical_json")), TEXT("ClothPhysicalMeshDataNv"), Why);
            const auto Config = SourceProperties(Input->GetStringField(TEXT("config_json")), TEXT("ClothConfigNv"), Why);
            if (!Properties || !Physical || !Config) return Failure(Why);
            UClothingAssetCommon* Asset = NewObject<UClothingAssetCommon>(Mesh,
                FName(*(TEXT("CA_") + Input->GetStringField(TEXT("name")))), RF_Transactional);
            FStructProperty* Guid = FindFProperty<FStructProperty>(UClothingAssetBase::StaticClass(), TEXT("AssetGuid"));
            if (!Guid) return Failure(TEXT("Engine clothing GUID field is unavailable"));
            *Guid->ContainerPtrToValuePtr<FGuid>(Asset) = FGuid::NewGuid();
            for (const auto& Bone : Properties->GetArrayField(TEXT("UsedBoneNames")))
            {
                const FName Name(*Bone->AsString());
                if (Mesh->GetRefSkeleton().FindBoneIndex(Name) == INDEX_NONE)
                    return Failure(TEXT("Cloth source bone is missing from exact target skeleton: ") + Name.ToString());
                Asset->UsedBoneNames.Add(Name);
            }
            Asset->LodData.AddDefaulted();
            if (!PhysicalMesh(Physical, Asset->UsedBoneNames.Num(), Live, Asset->LodData[0].PhysicalMeshData, Why))
                return Failure(Input->GetStringField(TEXT("name")) + TEXT(": ") + Why);
            Asset->PhysicsAsset = Physics;
            Asset->RefreshBoneMapping(Mesh);
            Asset->CalculateReferenceBoneIndex();
            // Omitted source constructor fields use the explicit stock legacy
            // defaults. The report identifies this as compatibility migration.
            FClothConfig_Legacy Legacy;
            if (!FJsonObjectConverter::JsonObjectToUStruct(Config.ToSharedRef(), FClothConfig_Legacy::StaticStruct(), &Legacy, 0, 0))
                return Failure(TEXT("Cannot migrate serialized Nv cloth parameters"));
            double Number;
            bool Boolean;
            if ((Config->TryGetNumberField(TEXT("SolverFrequency"), Number) && !FMath::IsNearlyEqual(double(Legacy.SolverFrequency), Number, 0.0001))
                || (Config->TryGetNumberField(TEXT("StiffnessFrequency"), Number) && !FMath::IsNearlyEqual(double(Legacy.StiffnessFrequency), Number, 0.0001))
                || (Config->TryGetBoolField(TEXT("bUseGravityOverride"), Boolean) && Legacy.bUseGravityOverride != Boolean))
                return Failure(TEXT("Serialized Nv config property did not survive legacy conversion"));
            UChaosClothConfig* Chaos = NewObject<UChaosClothConfig>(Asset, TEXT("PC_ChaosConfig"), RF_Transactional);
            Chaos->MigrateFrom(Legacy);
            Asset->ClothConfigs.Add(Chaos->GetClass()->GetFName(), Chaos);
            if (Live && !SharedConfigured) { Shared->MigrateFrom(Legacy); SharedConfigured = true; }
            Asset->ClothConfigs.Add(Shared->GetClass()->GetFName(), Shared);
            Mesh->AddClothingAsset(Asset);
            if (Live)
            {
                const int32 Material = Input->GetIntegerField(TEXT("material_index"));
                auto& Sections = Mesh->GetImportedModel()->LODModels[0].Sections;
                int32 SectionIndex = INDEX_NONE;
                for (int32 Index = 0; Index < Sections.Num(); ++Index)
                    if (Sections[Index].MaterialIndex == Material)
                    {
                        if (SectionIndex != INDEX_NONE) return Failure(TEXT("Source material splits into ambiguous render sections"));
                        SectionIndex = Index;
                    }
                if (SectionIndex == INDEX_NONE || !VerifyRenderSection(Sections[SectionIndex], Asset->LodData[0].PhysicalMeshData,
                    Input->GetIntegerField(TEXT("source_num_triangles")), Why)) return Failure(Why);
                auto& Lod = Asset->LodData[0];
                for (const auto& Map : Lod.PhysicalMeshData.WeightMaps)
                {
                    FPointWeightMap Mask;
                    Mask.Initialize(Map.Value, Map.Key);
                    Mask.Name = FName(*FString::Printf(TEXT("PC_Source_%u"), Map.Key));
                    Lod.PointWeightMaps.Add(Mask);
                }
                Asset->InvalidateAllCachedData();
                if (!Asset->BindToSkeletalMesh(Mesh, 0, SectionIndex, 0)) return Failure(TEXT("Native Chaos render section binding failed"));
                // Mirror SkeletalMeshClothingSystemUtilities::AssignClothingToSection.
                // BuildDerivedDataKey synchronizes sections FROM this user-data
                // map; binding only the model would be cleared by PostEditChange.
                auto& Model = Mesh->GetImportedModel()->LODModels[0];
                auto& User = Model.UserSectionsData.FindOrAdd(Model.Sections[SectionIndex].OriginalDataSectionIndex);
                int32 AssetIndex = INDEX_NONE;
                Mesh->GetMeshClothingAssets().Find(Asset, AssetIndex);
                User.CorrespondClothAssetIndex = static_cast<int16>(AssetIndex);
                User.ClothingData.AssetGuid = Asset->GetAssetGuid();
                User.ClothingData.AssetLodIndex = 0;
            }
        }
        for (UClothingAssetBase* Asset : Mesh->GetMeshClothingAssets()) Asset->PostUpdateAllAssets();
        Mesh->SetHasActiveClothingAssets(Mesh->ComputeActiveClothingAssets());
        Mesh->MarkPackageDirty();
        BeforeBuild = Describe(Mesh);
    }
    FSkinnedAssetCompilingManager::Get().FinishCompilation({Mesh});
    const auto Report = Describe(Mesh);
    if (!Mesh->HasActiveClothingAssets()) return Failure(TEXT("No live native clothing binding survived the mesh build. Before=")
        + (BeforeBuild ? Encode(BeforeBuild.ToSharedRef()) : FString()) + TEXT(" After=") + Encode(Report));
    const FString Filename = FPackageName::LongPackageNameToFilename(TargetMeshPackage, FPackageName::GetAssetPackageExtension());
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
    FSavePackageArgs Args;
    Args.TopLevelFlags = RF_Public | RF_Standalone;
    Args.SaveFlags = SAVE_NoError;
    if (!UPackage::SavePackage(Mesh->GetPackage(), Mesh, *Filename, Args)) return Failure(TEXT("Cannot save cloth mesh"));
    Report->SetStringField(TEXT("source_mesh"), SourceMeshPackage);
    return Encode(Report);
}

FString UPCViewerClothLibrary::InspectClothMesh(const FString& MeshPackage)
{
    USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *PCViewerCloth::ObjectPath(MeshPackage));
    return Mesh ? PCViewerCloth::Encode(PCViewerCloth::Describe(Mesh)) : PCViewerCloth::Failure(TEXT("Missing cloth mesh"));
}

FString UPCViewerClothLibrary::InspectClothComponent(USkeletalMeshComponent* Component)
{
    using namespace PCViewerCloth;
    if (!Component || !Component->GetSkeletalMeshAsset()) return Failure(TEXT("Missing cloth component"));
    Component->WaitForExistingParallelClothSimulation_GameThread();
    const auto Report = MakeShared<FJsonObject>();
    int32 ClothCount = 0, Kinematic = 0, Dynamic = 0;
    TArray<TSharedPtr<FJsonValue>> Instances;
    for (const auto& Instance : Component->GetClothingSimulationInstances())
    {
        const auto Row = MakeShared<FJsonObject>();
        const auto* Simulation = Instance.GetClothingSimulation();
        if (!Simulation) continue;
        const int32 Count = Simulation->GetNumCloths();
        Row->SetStringField(TEXT("factory"), Instance.GetClothingSimulationFactory()->GetClass()->GetPathName());
        Row->SetNumberField(TEXT("cloths"), Count);
        Row->SetNumberField(TEXT("kinematic_particles"), Simulation->GetNumKinematicParticles());
        Row->SetNumberField(TEXT("dynamic_particles"), Simulation->GetNumDynamicParticles());
        ClothCount += Count;
        Kinematic += Simulation->GetNumKinematicParticles();
        Dynamic += Simulation->GetNumDynamicParticles();
        Instances.Add(MakeShared<FJsonValueObject>(Row));
    }
    USkeletalMesh* Mesh = Component->GetSkeletalMeshAsset();
    const auto Reference = ReferenceTransforms(Mesh);
    const auto& Animated = Component->GetComponentSpaceTransforms();
    const auto& Data = Component->GetCurrentClothingData_AnyThread();
    bool Finite = true;
    int32 Vertices = 0, Pinned = 0;
    double PinnedError = 0., DynamicOffset = 0.;
    for (const auto& Entry : Data)
    {
        if (!Mesh->GetMeshClothingAssets().IsValidIndex(Entry.Key)) { Finite = false; continue; }
        const auto* Asset = Cast<UClothingAssetCommon>(Mesh->GetMeshClothingAssets()[Entry.Key]);
        if (!Asset || !Asset->LodData.IsValidIndex(Entry.Value.LODIndex)) { Finite = false; continue; }
        const auto& Physical = Asset->LodData[Entry.Value.LODIndex].PhysicalMeshData;
        const auto* Distances = Physical.FindWeightMap(EWeightMapTargetCommon::MaxDistance);
        if (!Distances || Physical.Vertices.Num() != Entry.Value.Positions.Num()
            || Entry.Value.Normals.Num() != Entry.Value.Positions.Num()) { Finite = false; continue; }
        for (int32 Index = 0; Index < Entry.Value.Positions.Num(); ++Index)
        {
            const FVector Position = Entry.Value.Transform.TransformPosition(FVector(Entry.Value.Positions[Index]));
            Finite &= !Position.ContainsNaN() && !Entry.Value.Normals[Index].ContainsNaN();
            FVector Expected = FVector::ZeroVector;
            const auto& Bones = Physical.BoneData[Index];
            for (int32 Influence = 0; Influence < Bones.NumInfluences; ++Influence)
            {
                const int32 Bone = Asset->UsedBoneIndices[Bones.BoneIndices[Influence]];
                if (!Animated.IsValidIndex(Bone)) { Finite = false; continue; }
                Expected += Animated[Bone].TransformPosition(Reference[Bone].InverseTransformPosition(FVector(Physical.Vertices[Index])))
                    * Bones.BoneWeights[Influence];
            }
            Expected = Component->GetComponentTransform().TransformPosition(Expected);
            const double Offset = FVector::Distance(Position, Expected);
            if ((*Distances)[Index] == 0.f) { ++Pinned; PinnedError = FMath::Max(PinnedError, Offset); }
            else DynamicOffset = FMath::Max(DynamicOffset, Offset);
            ++Vertices;
        }
    }
    Report->SetStringField(TEXT("status"), Finite && ClothCount > 0 && Vertices > 0 ? TEXT("passed") : TEXT("no_live_cloth_data"));
    Report->SetArrayField(TEXT("instances"), Instances);
    Report->SetNumberField(TEXT("cloth_count"), ClothCount);
    Report->SetNumberField(TEXT("kinematic_particles"), Kinematic);
    Report->SetNumberField(TEXT("dynamic_particles"), Dynamic);
    Report->SetNumberField(TEXT("data_sets"), Data.Num());
    Report->SetNumberField(TEXT("vertices"), Vertices);
    Report->SetNumberField(TEXT("pinned_vertices_checked"), Pinned);
    Report->SetNumberField(TEXT("pinned_error_max_cm"), PinnedError);
    Report->SetNumberField(TEXT("dynamic_skinned_offset_max_cm"), DynamicOffset);
    Report->SetBoolField(TEXT("finite"), Finite);
    return Encode(Report);
}
