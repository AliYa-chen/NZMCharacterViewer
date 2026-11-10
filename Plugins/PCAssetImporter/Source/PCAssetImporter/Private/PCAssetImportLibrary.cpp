#include "PCAssetImportLibrary.h"

#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "EditorFramework/AssetImportData.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "IMeshBuilderModule.h"
#include "Interfaces/ITargetPlatformManagerModule.h"
#include "Materials/Material.h"
#include "MeshDescription.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Rendering/SkeletalMeshLODImporterData.h"
#include "Rendering/SkeletalMeshLODModel.h"
#include "Rendering/SkeletalMeshModel.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SkeletalMeshAttributes.h"
#include "SkinnedAssetCompiler.h"
#include "StaticMeshOperations.h"
#include "UObject/MetaData.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectHash.h"

namespace PCActorX
{
constexpr const TCHAR* OwnerKey = TEXT("PCAssetImporter.Owner");
constexpr const TCHAR* OwnerValue = TEXT("PCAssetImporter/v1");
constexpr const TCHAR* HashKey = TEXT("PCAssetImporter.SourceSHA256");
constexpr double WeightTolerance = 4.0 / 65535.0;
// Match TVector<float>::GetSafeNormal's minimum squared length. This is a
// verification classification for safely normalizable geometric normals, not
// a claim about MikkTSpace's internal degenerate-triangle handling.
constexpr float GeometryNormalSquaredTolerance = UE_SMALL_NUMBER;
constexpr float UVDeterminantTolerance = 1.e-12f;

static FString Json(const TSharedRef<FJsonObject>& Object)
{
    FString Result;
    FJsonSerializer::Serialize(Object, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

static FString FailureJson(const FString& Message)
{
    const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
    Object->SetStringField(TEXT("status"), TEXT("failed"));
    Object->SetStringField(TEXT("error"), Message);
    UE_LOG(LogTemp, Error, TEXT("PCAssetImporter: %s"), *Message);
    return Json(Object);
}

// A small portable SHA256 avoids platform-specific hashing APIs: Mac's
// FGenericPlatformMisc::GetSHA256Signature has no implementation in this UE.
static FString SHA256(const TArray<uint8>& Bytes)
{
    static const uint32 K[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    };
    uint32 H[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    auto R = [](uint32 V, uint32 N) { return (V >> N) | (V << (32-N)); };
    TArray<uint8> Padded(Bytes);
    Padded.Add(0x80);
    while ((Padded.Num() % 64) != 56) Padded.Add(0);
    const uint64 Bits = uint64(Bytes.Num()) * 8;
    for (int32 I=7; I>=0; --I) Padded.Add(uint8(Bits >> (I*8)));
    for (int32 Offset=0; Offset<Padded.Num(); Offset+=64)
    {
        uint32 W[64];
        for (int32 I=0; I<16; ++I)
        {
            const uint8* P = Padded.GetData()+Offset+4*I;
            W[I] = uint32(P[0])<<24 | uint32(P[1])<<16 | uint32(P[2])<<8 | P[3];
        }
        for (int32 I=16; I<64; ++I)
        {
            const uint32 S0=R(W[I-15],7)^R(W[I-15],18)^(W[I-15]>>3);
            const uint32 S1=R(W[I-2],17)^R(W[I-2],19)^(W[I-2]>>10);
            W[I]=W[I-16]+S0+W[I-7]+S1;
        }
        uint32 A=H[0],B=H[1],C=H[2],D=H[3],E=H[4],F=H[5],G=H[6],V=H[7];
        for (int32 I=0; I<64; ++I)
        {
            const uint32 T1=V+(R(E,6)^R(E,11)^R(E,25))+((E&F)^(~E&G))+K[I]+W[I];
            const uint32 T2=(R(A,2)^R(A,13)^R(A,22))+((A&B)^(A&C)^(B&C));
            V=G;G=F;F=E;E=D+T1;D=C;C=B;B=A;A=T1+T2;
        }
        H[0]+=A;H[1]+=B;H[2]+=C;H[3]+=D;H[4]+=E;H[5]+=F;H[6]+=G;H[7]+=V;
    }
    FString Result;
    for (uint32 Value : H) Result += FString::Printf(TEXT("%08x"),Value);
    return Result;
}

static uint32 U32(const uint8* P)
{
    return uint32(P[0]) | uint32(P[1])<<8 | uint32(P[2])<<16 | uint32(P[3])<<24;
}
static uint16 U16(const uint8* P) { return uint16(P[0]) | uint16(P[1])<<8; }
static float Float(const uint8* P)
{
    const uint32 Bits=U32(P);
    float Value;
    FMemory::Memcpy(&Value,&Bits,4);
    return Value;
}
static FVector3f Vector(const uint8* P) { return FVector3f(Float(P),-Float(P+4),Float(P+8)); }
static FString Text(const uint8* P, int32 Size)
{
    int32 Count=0;
    while (Count<Size && P[Count]!=0) ++Count;
    const FUTF8ToTCHAR Converted(reinterpret_cast<const ANSICHAR*>(P),Count);
    return FString(Converted.Length(),Converted.Get());
}

struct FActorX
{
    FSkeletalMeshImportData Data;
    TArray<FVector3f> Normals;
    TArray<TArray<SkeletalMeshImportData::FRawBoneInfluence>> Weights;
    FString Hash;
    FString Filename;
    TArray<FString> Chunks;
    TArray<FString> OriginalMaterialNames;
    int32 MaximumInfluences=0;
};

static bool Parse(const FString& Filename, FActorX& Out, FString& Why)
{
    TArray<uint8> Bytes;
    Out.Filename=FPaths::ConvertRelativePathToFull(Filename);
    if (!FFileHelper::LoadFileToArray(Bytes,*Out.Filename)) { Why=TEXT("Cannot read ActorX source: ")+Filename;return false; }
    Out.Hash=SHA256(Bytes);
    TSet<FString> Seen;
    TMap<int32,TArray<FVector2f>> ExtraUVs;
    TArray<FColor> Colors;
    int64 Offset=0;
    while (Offset<Bytes.Num())
    {
        if (Bytes.Num()-Offset<32) { Why=TEXT("Truncated ActorX header");return false; }
        const uint8* Header=Bytes.GetData()+Offset;
        const FString Name=Text(Header,20);
        const int32 Size=int32(U32(Header+24)),Count=int32(U32(Header+28));
        Offset+=32;
        if (Size<0 || Count<0 || int64(Size)*Count>Bytes.Num()-Offset || Seen.Contains(Name))
        { Why=TEXT("Invalid or duplicate ActorX chunk: ")+Name;return false; }
        Seen.Add(Name);Out.Chunks.Add(Name);
        const uint8* Body=Bytes.GetData()+Offset;
        auto RequireSize=[&](int32 Expected) { if(Size!=Expected) { Why=TEXT("Unexpected row size: ")+Name;return false; } return true; };
        if (Name==TEXT("ACTRHEAD"))
        {
            if (Offset!=32 || Size!=0 || Count!=0) { Why=TEXT("Invalid ActorX signature");return false; }
        }
        else if (Name==TEXT("PNTS0000"))
        {
            if (!RequireSize(12)) return false;
            Out.Data.Points.Reserve(Count);
            for (int32 I=0;I<Count;++I) Out.Data.Points.Add(Vector(Body+I*Size));
        }
        else if (Name==TEXT("VTXW0000"))
        {
            if (!RequireSize(16)) return false;
            Out.Data.Wedges.SetNum(Count);
            for (int32 I=0;I<Count;++I)
            {
                const uint8* P=Body+I*Size;
                auto& Wedge=Out.Data.Wedges[I];
                Wedge.VertexIndex=U32(P);Wedge.UVs[0]=FVector2f(Float(P+4),Float(P+8));
                Wedge.MatIndex=P[12];Wedge.Color=FColor::White;
            }
        }
        else if (Name==TEXT("FACE0000") || Name==TEXT("FACE3200"))
        {
            const bool Wide=Name==TEXT("FACE3200");
            if (!RequireSize(Wide?18:12)) return false;
            Out.Data.Faces.SetNumZeroed(Count);
            for (int32 I=0;I<Count;++I)
            {
                const uint8* P=Body+I*Size;auto& Face=Out.Data.Faces[I];
                // Reverse the writer's mirror, including triangle winding.
                Face.WedgeIndex[0]=Wide?U32(P+4):U16(P+2);
                Face.WedgeIndex[1]=Wide?U32(P):U16(P);
                Face.WedgeIndex[2]=Wide?U32(P+8):U16(P+4);
                Face.MatIndex=U16(P+(Wide?12:6));
                Face.SmoothingGroups=U32(P+(Wide?14:8));
                Out.Data.MaxMaterialIndex=FMath::Max(Out.Data.MaxMaterialIndex,uint32(Face.MatIndex));
            }
        }
        else if (Name==TEXT("MATT0000"))
        {
            if (!RequireSize(88)) return false;
            Out.Data.Materials.SetNum(Count);
            for (int32 I=0;I<Count;++I)
            {
                Out.Data.Materials[I].MaterialImportName=Text(Body+I*Size,64);
                Out.OriginalMaterialNames.Add(Out.Data.Materials[I].MaterialImportName);
            }
        }
        else if (Name==TEXT("VTXNORMS"))
        {
            if (!RequireSize(12)) return false;
            Out.Normals.Reserve(Count);
            for (int32 I=0;I<Count;++I) Out.Normals.Add(Vector(Body+I*Size));
        }
        else if (Name==TEXT("RAWWEIGHTS"))
        {
            if (!RequireSize(12)) return false;
            Out.Data.Influences.SetNum(Count);
            for (int32 I=0;I<Count;++I)
            {
                const uint8* P=Body+I*Size;auto& Influence=Out.Data.Influences[I];
                Influence.Weight=Float(P);Influence.VertexIndex=int32(U32(P+4));Influence.BoneIndex=int32(U32(P+8));
            }
        }
        else if (Name==TEXT("REFSKELT"))
        {
            if (!RequireSize(120)) return false;
            Out.Data.RefBonesBinary.SetNumZeroed(Count);
            for (int32 I=0;I<Count;++I)
            {
                const uint8* P=Body+I*Size;auto& Bone=Out.Data.RefBonesBinary[I];
                Bone.Name=Text(P,64);Bone.Flags=U32(P+64);Bone.NumChildren=int32(U32(P+68));Bone.ParentIndex=int32(U32(P+72));
                FQuat4f Q(Float(P+76),-Float(P+80),Float(P+84),(I==0?-1.f:1.f)*Float(P+88));
                if(Q.ContainsNaN() || Q.SizeSquared()<0.99f || Q.SizeSquared()>1.01f) { Why=TEXT("Invalid bind quaternion");return false; }
                // PSK has no local bone scale. The fixed upstream writer bakes
                // inherited scale into translations; preserve that exported bind.
                Bone.BonePos.Transform=FTransform3f(Q.GetNormalized(),Vector(P+92),FVector3f::OneVector);
                Bone.BonePos.Length=Float(P+104);Bone.BonePos.XSize=Float(P+108);
                Bone.BonePos.YSize=Float(P+112);Bone.BonePos.ZSize=Float(P+116);
            }
        }
        else if (Name==TEXT("VERTEXCOLOR"))
        {
            if (!RequireSize(4)) return false;
            Colors.Reserve(Count);
            // CUE4Parse FColor.Serialize writes R,G,B,A, rather than native BGRA.
            for(int32 I=0;I<Count;++I) { const uint8* P=Body+4*I;Colors.Add(FColor(P[0],P[1],P[2],P[3])); }
        }
        else if (Name.StartsWith(TEXT("EXTRAUVS")))
        {
            if (!RequireSize(8)) return false;
            const FString Suffix=Name.Mid(8);
            if (Suffix.Len()!=1 || Suffix[0]<'0' || Suffix[0]>'6') { Why=TEXT("Unsupported extra UV channel");return false; }
            const int32 Channel=Suffix[0]-'0'+1;
            TArray<FVector2f>& UVs=ExtraUVs.Add(Channel);UVs.Reserve(Count);
            for (int32 I=0;I<Count;++I) UVs.Add(FVector2f(Float(Body+I*Size),Float(Body+I*Size+4)));
        }
        else { Why=TEXT("Unsupported ActorX chunk; refusing to discard data: ")+Name;return false; }
        Offset+=int64(Size)*Count;
    }
    for (const TCHAR* Required : {TEXT("ACTRHEAD"),TEXT("PNTS0000"),TEXT("VTXW0000"),TEXT("MATT0000"),TEXT("VTXNORMS"),TEXT("RAWWEIGHTS"),TEXT("REFSKELT")})
        if (!Seen.Contains(Required)) { Why=FString(TEXT("Missing required ActorX chunk: "))+Required;return false; }
    if (Out.Data.Points.IsEmpty() || Out.Data.Faces.IsEmpty() || Out.Data.RefBonesBinary.IsEmpty() ||
        Out.Data.Materials.IsEmpty() || Out.Normals.Num()!=Out.Data.Points.Num() ||
        (!Colors.IsEmpty() && Colors.Num()!=Out.Data.Points.Num()))
    { Why=TEXT("Incomplete ActorX geometry, normals, colors or skeleton");return false; }
    Out.Data.NumTexCoords=1;Out.Data.bHasNormals=true;Out.Data.bHasTangents=false;Out.Data.bHasVertexColors=!Colors.IsEmpty();
    for (const auto& Pair:ExtraUVs)
    {
        if (Pair.Value.Num()!=Out.Data.Wedges.Num() || Pair.Key>=MAX_TEXCOORDS) { Why=TEXT("Extra UV row count/channel mismatch");return false; }
        Out.Data.NumTexCoords=FMath::Max(Out.Data.NumTexCoords,uint32(Pair.Key+1));
    }
    for (uint32 Channel=1;Channel<Out.Data.NumTexCoords;++Channel)
        if (!ExtraUVs.Contains(Channel)) { Why=TEXT("Non-contiguous UV channels");return false; }
    for (int32 I=0;I<Out.Data.Points.Num();++I)
    {
        if(Out.Data.Points[I].ContainsNaN() || Out.Normals[I].ContainsNaN() || Out.Normals[I].IsNearlyZero())
        { Why=TEXT("Nonfinite point/normal");return false; }
        Out.Data.PointToRawMap.Add(I);
    }
    for (int32 I=0;I<Out.Data.Wedges.Num();++I)
    {
        auto& Wedge=Out.Data.Wedges[I];
        if (!Out.Data.Points.IsValidIndex(Wedge.VertexIndex) || Wedge.UVs[0].ContainsNaN()) { Why=TEXT("Invalid wedge point/UV");return false; }
        if(!Colors.IsEmpty()) Wedge.Color=Colors[Wedge.VertexIndex];
        for (const auto& Pair:ExtraUVs)
        {
            Wedge.UVs[Pair.Key]=Pair.Value[I];
            if(Wedge.UVs[Pair.Key].ContainsNaN()) { Why=TEXT("Nonfinite extra UV");return false; }
        }
    }
    for(auto& Face:Out.Data.Faces)
    {
        if (!Out.Data.Materials.IsValidIndex(Face.MatIndex)) { Why=TEXT("Face material out of range");return false; }
        for(int32 Corner=0;Corner<3;++Corner)
        {
            if(!Out.Data.Wedges.IsValidIndex(Face.WedgeIndex[Corner])) { Why=TEXT("Face wedge out of range");return false; }
            Face.TangentZ[Corner]=Out.Normals[Out.Data.Wedges[Face.WedgeIndex[Corner]].VertexIndex];
        }
    }
    TSet<FName> BoneNames;
    for(int32 I=0;I<Out.Data.RefBonesBinary.Num();++I)
    {
        const auto& Bone=Out.Data.RefBonesBinary[I];const FName Name(*Bone.Name);
        if(Name.IsNone() || BoneNames.Contains(Name) || Bone.BonePos.Transform.ContainsNaN() ||
           (I==0?Bone.ParentIndex!=INDEX_NONE:(Bone.ParentIndex<0 || Bone.ParentIndex>=I)))
        { Why=TEXT("Invalid or duplicate bone hierarchy: ")+Bone.Name;return false; }
        BoneNames.Add(Name);
    }
    Out.Weights.SetNum(Out.Data.Points.Num());
    for(const auto& Influence:Out.Data.Influences)
    {
        if(!Out.Weights.IsValidIndex(Influence.VertexIndex) || !Out.Data.RefBonesBinary.IsValidIndex(Influence.BoneIndex) ||
           !FMath::IsFinite(Influence.Weight) || Influence.Weight<=0.f || Influence.Weight>1.f)
        { Why=TEXT("Invalid skin influence");return false; }
        Out.Weights[Influence.VertexIndex].Add(Influence);
    }
    for (const auto& VertexWeights:Out.Weights)
    {
        if(VertexWeights.IsEmpty() || VertexWeights.Num()>8) { Why=TEXT("Missing skin weights or more than eight influences");return false; }
        float Sum=0.f;TSet<int32> Bones;
        for(const auto& Influence:VertexWeights)
        {
            if(Bones.Contains(Influence.BoneIndex)) { Why=TEXT("Duplicate vertex bone influence");return false; }
            Bones.Add(Influence.BoneIndex);Sum+=Influence.Weight;
        }
        if(FMath::Abs(Sum-1.f)>0.0001f) { Why=TEXT("Source weights are not normalized; no silent correction permitted");return false; }
        Out.MaximumInfluences=FMath::Max(Out.MaximumInfluences,VertexWeights.Num());
    }
    return true;
}

static bool TargetAllowed(const FString& Package)
{
    return Package.StartsWith(TEXT("/Game/PCAssets/")) && FPackageName::IsValidLongPackageName(Package) && !Package.Contains(TEXT("."));
}

static bool Owned(UObject* Object)
{
    return Object && Object->GetPackage()->GetMetaData().GetValue(Object,OwnerKey)==OwnerValue;
}

static bool PackageHasUnpublishedContent(const FString& PackageName)
{
    UPackage* Package=FindPackage(nullptr,*PackageName);
    if(!Package) return false;
    if(Package->IsDirty()) return true;
    TArray<UObject*> Objects;
    GetObjectsWithOuter(Package,Objects,EGetObjectsFlags::None);
    // A failed lookup may leave an empty, clean UPackage. It is safe to reuse
    // that shell, but any real top-level object still blocks a new import.
    return Objects.ContainsByPredicate([](UObject* Object) { return Object && !Object->HasAnyFlags(RF_Transient); });
}

static void Tag(UObject* Object,const FActorX& Source,const FString& SourcePackage)
{
    FMetaData& Meta=Object->GetPackage()->GetMetaData();
    Meta.SetValue(Object,OwnerKey,OwnerValue);Meta.SetValue(Object,HashKey,*Source.Hash);
    FString Relative=Source.Filename;FPaths::MakePathRelativeTo(Relative,*FPaths::ProjectDir());
    Meta.SetValue(Object,TEXT("PCAssetImporter.SourceFile"),*Relative);
    Meta.SetValue(Object,TEXT("PCAssetImporter.SourcePackage"),*SourcePackage);
    Meta.SetValue(Object,TEXT("PCAssetImporter.OriginalBoneScale"),TEXT("Not present in PSK; parent scale baked into exported translations"));
    Meta.SetValue(Object,TEXT("PCAssetImporter.Tangents"),TEXT("Recomputed with MikkTSpace; tangent vectors not exported by ActorX writer"));
}

static bool Save(UObject* Object)
{
    UPackage* Package=Object->GetPackage();
    const FString Filename=FPackageName::LongPackageNameToFilename(Package->GetName(),FPackageName::GetAssetPackageExtension());
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename),true);
    FSavePackageArgs Args;Args.TopLevelFlags=RF_Public|RF_Standalone;Args.SaveFlags=SAVE_NoError;
    Package->MarkPackageDirty();
    return UPackage::SavePackage(Package,Object,*Filename,Args);
}

static FString TriangleKey(uint32 Material,uint32 A,uint32 B,uint32 C)
{
    // A cyclic rotation preserves winding; a reversed triangle remains distinct.
    if(B<A && B<=C) { const uint32 OldA=A;A=B;B=C;C=OldA; }
    else if(C<A && C<B) { const uint32 OldA=A;A=C;C=B;B=OldA; }
    return FString::Printf(TEXT("%u:%u,%u,%u"),Material,A,B,C);
}

static bool ResolveMaterialNames(const FActorX& Source,const TSharedPtr<FJsonObject>& Options,
    TArray<FString>& DisplayNames,TArray<FString>& ImportNames,FString& Why)
{
    DisplayNames=Source.OriginalMaterialNames;
    const TArray<TSharedPtr<FJsonValue>>* SlotNames=nullptr;
    if(Options.IsValid() && Options->TryGetArrayField(TEXT("slot_names"),SlotNames))
    {
        if(SlotNames->Num()!=DisplayNames.Num()) { Why=TEXT("slot_names count differs from ActorX section count");return false; }
        for(int32 I=0;I<SlotNames->Num();++I)
        {
            if((*SlotNames)[I]->Type==EJson::Null) continue;
            FString Name;
            if(!(*SlotNames)[I]->TryGetString(Name)) { Why=TEXT("slot_names entries must be strings or null");return false; }
            if(!Name.IsEmpty()) DisplayNames[I]=Name;
        }
    }
    TSet<FName> Used;
    for(int32 I=0;I<DisplayNames.Num();++I)
    {
        if(DisplayNames[I].IsEmpty()) DisplayNames[I]=FString::Printf(TEXT("MaterialSlot_%d"),I);
        FString ImportName=DisplayNames[I];
        if(Used.Contains(FName(*ImportName))) ImportName+=FString::Printf(TEXT("_SourceSlot_%d"),I);
        if(Used.Contains(FName(*ImportName))) { Why=TEXT("Ambiguous material import slot names");return false; }
        Used.Add(FName(*ImportName));ImportNames.Add(ImportName);
    }
    return true;
}

static bool Verify(const FActorX& Source,USkeletalMesh* Mesh,TSharedRef<FJsonObject> Report,FString& Why,
    const TSharedPtr<FJsonObject>& RequestedOptions=nullptr,const bool CheckBuiltTangents=true)
{
    if(!Mesh || !Owned(Mesh)) { Why=TEXT("Target is not an importer-owned SkeletalMesh");return false; }
    if(Mesh->GetPackage()->GetMetaData().GetValue(Mesh,HashKey)!=Source.Hash)
    { Why=TEXT("Mesh source SHA256 differs");return false; }
    FSkinnedAssetCompilingManager::Get().FinishCompilation({Mesh});
    const FReferenceSkeleton& Ref=Mesh->GetRefSkeleton();
    if(Ref.GetRawBoneNum()!=Source.Data.RefBonesBinary.Num() || !Mesh->GetSkeleton())
    { Why=TEXT("Imported bone count or Skeleton is missing");return false; }
    if(Mesh->GetMaterials().Num()!=Source.Data.Materials.Num())
    { Why=TEXT("Imported material slot count differs");return false; }
    TSharedPtr<FJsonObject> MaterialOptions=RequestedOptions;
    if(!MaterialOptions.IsValid())
    {
        const FString StoredOptions=Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.OptionsJSON"));
        if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(StoredOptions.IsEmpty()?TEXT("{}"):StoredOptions),MaterialOptions))
        { Why=TEXT("Saved material slot options are invalid");return false; }
    }
    TArray<FString> ExpectedDisplayNames,ExpectedImportNames;
    if(!ResolveMaterialNames(Source,MaterialOptions,ExpectedDisplayNames,ExpectedImportNames,Why)) return false;
    for(int32 I=0;I<Mesh->GetMaterials().Num();++I)
    {
        const FSkeletalMaterial& Material=Mesh->GetMaterials()[I];
        if(Material.MaterialSlotName!=FName(*ExpectedDisplayNames[I]) || Material.ImportedMaterialSlotName!=FName(*ExpectedImportNames[I]))
        { Why=FString::Printf(TEXT("Material slot name/order differs at %d"),I);return false; }
    }
    for(int32 I=0;I<Ref.GetRawBoneNum();++I)
    {
        const auto& Bone=Source.Data.RefBonesBinary[I];
        if(Ref.GetRawRefBoneInfo()[I].Name!=FName(*Bone.Name) || Ref.GetRawParentIndex(I)!=Bone.ParentIndex ||
           !Ref.GetRawRefBonePose()[I].Equals(FTransform(Bone.BonePos.Transform),0.00002))
        { Why=FString::Printf(TEXT("Bind bone mismatch at %d"),I);return false; }
    }
    const FMeshDescription* Description=Mesh->GetMeshDescription(0);
    if(!Description || Description->Vertices().Num()!=Source.Data.Points.Num() ||
       Description->Triangles().Num()!=Source.Data.Faces.Num())
    { Why=TEXT("Saved source geometry count mismatch");return false; }
    const FSkeletalMeshConstAttributes Attributes(*Description);
    const auto Positions=Attributes.GetVertexPositions();
    const auto UVs=Attributes.GetVertexInstanceUVs();
    const auto Normals=Attributes.GetVertexInstanceNormals();
    const auto Colors=Attributes.GetVertexInstanceColors();
    const auto SkinWeights=Attributes.GetVertexSkinWeights();
    if(UVs.GetNumChannels()!=Source.Data.NumTexCoords) { Why=TEXT("Saved UV channel count mismatch");return false; }
    int64 VerifiedWeights=0;
    for(int32 Point=0;Point<Source.Data.Points.Num();++Point)
    {
        const FVertexID Vertex(Point);
        if(!Positions[Vertex].Equals(Source.Data.Points[Point],0.f)) { Why=TEXT("Saved vertex position changed");return false; }
        const auto Weights=SkinWeights.Get(Vertex);
        if(Weights.Num()!=Source.Weights[Point].Num()) { Why=TEXT("Saved source skin influence count changed");return false; }
        for(const auto& Weight:Weights)
        {
            const auto* Expected=Source.Weights[Point].FindByPredicate([&](const auto& Item) { return Item.BoneIndex==Weight.GetBoneIndex(); });
            if(!Expected || FMath::Abs(double(Expected->Weight)-Weight.GetWeight())>WeightTolerance)
            { Why=TEXT("Saved source skin influence differs beyond 16-bit quantization tolerance");return false; }
            ++VerifiedWeights;
        }
    }
    TArray<TArray<int32>> PointWedges;PointWedges.SetNum(Source.Data.Points.Num());
    for(int32 I=0;I<Source.Data.Wedges.Num();++I) PointWedges[Source.Data.Wedges[I].VertexIndex].Add(I);
    for(const FVertexInstanceID Instance:Description->VertexInstances().GetElementIDs())
    {
        const int32 Point=Description->GetVertexInstanceVertex(Instance).GetValue();
        if(!Normals[Instance].Equals(Source.Normals[Point],0.000001f)) { Why=TEXT("Saved vertex normal changed");return false; }
        bool Matched=false;
        for(const int32 WedgeIndex:PointWedges[Point])
        {
            const auto& Wedge=Source.Data.Wedges[WedgeIndex];bool UVMatch=true;
            for(uint32 Channel=0;Channel<Source.Data.NumTexCoords;++Channel)
                UVMatch &= UVs.Get(Instance,Channel).Equals(Wedge.UVs[Channel],0.000001f);
            const FLinearColor Color=Wedge.Color.ReinterpretAsLinear();
            const FVector4f ExpectedColor(Color.R,Color.G,Color.B,Color.A);
            if(UVMatch && (!Source.Data.bHasVertexColors || Colors[Instance].Equals(ExpectedColor,0.000001f))) { Matched=true;break; }
        }
        if(!Matched) { Why=TEXT("Saved corner UV or vertex color changed");return false; }
    }
    TMap<FString,int32> Triangles;
    for(const auto& Face:Source.Data.Faces)
    {
        const FString Key=TriangleKey(Face.MatIndex,Source.Data.Wedges[Face.WedgeIndex[0]].VertexIndex,
            Source.Data.Wedges[Face.WedgeIndex[1]].VertexIndex,Source.Data.Wedges[Face.WedgeIndex[2]].VertexIndex);
        ++Triangles.FindOrAdd(Key);
    }
    for(const FTriangleID Triangle:Description->Triangles().GetElementIDs())
    {
        const TArrayView<const FVertexInstanceID> Instances=Description->GetTriangleVertexInstances(Triangle);
        const FString Key=TriangleKey(Description->GetTrianglePolygonGroup(Triangle).GetValue(),
            Description->GetVertexInstanceVertex(Instances[0]).GetValue(),Description->GetVertexInstanceVertex(Instances[1]).GetValue(),
            Description->GetVertexInstanceVertex(Instances[2]).GetValue());
        int32* Remaining=Triangles.Find(Key);
        if(!Remaining || *Remaining<=0) { Why=TEXT("Saved triangle winding or material assignment differs");return false; }
        --*Remaining;
    }
    for(const auto& Pair:Triangles) if(Pair.Value!=0) { Why=TEXT("Missing saved triangle");return false; }

    const FSkeletalMeshModel* Model=Mesh->GetImportedModel();
    if(!Model || Model->LODModels.Num()!=1) { Why=TEXT("Built LOD is missing");return false; }
    const FSkeletalMeshLODModel& LOD=Model->LODModels[0];
    int64 RenderVertices=0,RenderTriangles=0,RenderWeights=0;
    int64 TangentFramesChecked=0,DegenerateFrameExceptions=0,NearDegenerateGeometryFrameExceptions=0,BadTangentFrames=0;
    double MaximumNormalError=0,MaximumNormalLengthError=0,MaximumTangentNormalDot=0;
    TArray<bool> ValidUVGeometry;ValidUVGeometry.Init(false,LOD.NumVertices);
    TArray<bool> NearDegenerateGeometry;NearDegenerateGeometry.Init(false,LOD.NumVertices);
    for(const FSkelMeshSection& Section:LOD.Sections)
    {
        for(uint32 Face=0;Face<Section.NumTriangles;++Face)
        {
            const int32 Offset=int32(Section.BaseIndex+3*Face);
            if(!LOD.IndexBuffer.IsValidIndex(Offset+2)) { Why=TEXT("Built triangle index range is invalid");return false; }
            uint32 Indices[3];const FSoftSkinVertex* Vertices[3];
            for(int32 Corner=0;Corner<3;++Corner)
            {
                Indices[Corner]=LOD.IndexBuffer[Offset+Corner];
                const int32 Local=int32(Indices[Corner])-int32(Section.BaseVertexIndex);
                if(!Section.SoftVertices.IsValidIndex(Local)) { Why=TEXT("Built triangle section vertex is invalid");return false; }
                Vertices[Corner]=&Section.SoftVertices[Local];
            }
            const FVector2f UV1=Vertices[1]->UVs[0]-Vertices[0]->UVs[0],UV2=Vertices[2]->UVs[0]-Vertices[0]->UVs[0];
            const float UVDeterminant=UV1.X*UV2.Y-UV1.Y*UV2.X;
            const FVector3f Cross=FVector3f::CrossProduct(Vertices[1]->Position-Vertices[0]->Position,Vertices[2]->Position-Vertices[0]->Position);
            const float CrossLengthSquared=Cross.SizeSquared();
            if(!FMath::IsFinite(UVDeterminant) || !FMath::IsFinite(CrossLengthSquared))
            { Why=TEXT("Built triangle geometry or UV determinant is not finite");return false; }
            // GetSafeNormal returns zero only below the tolerance, so equality
            // remains stable geometry. Preserve every triangle and source value.
            if(FMath::Abs(UVDeterminant)>UVDeterminantTolerance && CrossLengthSquared>=GeometryNormalSquaredTolerance)
            {
                for(uint32 Vertex:Indices) if(ValidUVGeometry.IsValidIndex(Vertex)) ValidUVGeometry[Vertex]=true;
            }
            else if(CrossLengthSquared>0.f && CrossLengthSquared<GeometryNormalSquaredTolerance)
            {
                for(uint32 Vertex:Indices) if(NearDegenerateGeometry.IsValidIndex(Vertex)) NearDegenerateGeometry[Vertex]=true;
            }
        }
    }
    for(const FSkelMeshSection& Section:LOD.Sections)
    {
        RenderTriangles+=Section.NumTriangles;
        for(int32 I=0;I<Section.SoftVertices.Num();++I)
        {
            const int32 RenderIndex=int32(Section.BaseVertexIndex)+I;
            if(!LOD.MeshToImportVertexMap.IsValidIndex(RenderIndex)) { Why=TEXT("Built vertex has no source point map");return false; }
            const int32 Point=LOD.MeshToImportVertexMap[RenderIndex];
            if(!Source.Data.Points.IsValidIndex(Point)) { Why=TEXT("Built vertex source point is invalid");return false; }
            const FSoftSkinVertex& Vertex=Section.SoftVertices[I];
            if(!Vertex.Position.Equals(Source.Data.Points[Point],0.00002f)) { Why=TEXT("Built vertex position changed");return false; }
            const FVector3f Normal(Vertex.TangentZ.X,Vertex.TangentZ.Y,Vertex.TangentZ.Z);
            const FVector3f ExpectedNormal=Source.Normals[Point].GetSafeNormal();
            const double NormalError=(Normal-ExpectedNormal).GetAbsMax();
            const double NormalLengthError=FMath::Abs(double(Normal.SizeSquared())-1.0);
            MaximumNormalError=FMath::Max(MaximumNormalError,NormalError);
            MaximumNormalLengthError=FMath::Max(MaximumNormalLengthError,NormalLengthError);
            if(Normal.ContainsNaN() || NormalError>0.0002 || NormalLengthError>0.001)
            { Why=TEXT("Built vertex normal differs from the normalized source normal");return false; }
            const FVector3f X=Vertex.TangentX,Y=Vertex.TangentY;
            const double Orthogonality=FMath::Max(FMath::Abs(double(FVector3f::DotProduct(X,Normal))),FMath::Abs(double(FVector3f::DotProduct(Y,Normal))));
            MaximumTangentNormalDot=FMath::Max(MaximumTangentNormalDot,Orthogonality);
            const bool ValidFrame=!X.ContainsNaN() && !Y.ContainsNaN() && !X.IsNearlyZero() && !Y.IsNearlyZero() &&
                FMath::Abs(X.SizeSquared()-1.f)<0.001f && FMath::Abs(Y.SizeSquared()-1.f)<0.001f && Orthogonality<=0.001;
            if(ValidFrame) ++TangentFramesChecked;
            else if(!X.ContainsNaN() && !Y.ContainsNaN() && !ValidUVGeometry[RenderIndex])
            {
                ++DegenerateFrameExceptions;
                if(NearDegenerateGeometry[RenderIndex]) ++NearDegenerateGeometryFrameExceptions;
            }
            else
            {
                ++BadTangentFrames;
                if(CheckBuiltTangents)
                {
                    const TSharedRef<FJsonObject> Detail=MakeShared<FJsonObject>();
                    Detail->SetNumberField(TEXT("render_vertex"),RenderIndex);
                    Detail->SetNumberField(TEXT("source_point"),Point);
                    Detail->SetNumberField(TEXT("section_material"),Section.MaterialIndex);
                    Detail->SetStringField(TEXT("position"),Vertex.Position.ToString());
                    Detail->SetStringField(TEXT("tangent_x"),X.ToString());
                    Detail->SetStringField(TEXT("tangent_y"),Y.ToString());
                    Detail->SetStringField(TEXT("normal"),Normal.ToString());
                    Detail->SetStringField(TEXT("source_normal"),ExpectedNormal.ToString());
                    Detail->SetNumberField(TEXT("tangent_x_length_squared"),X.SizeSquared());
                    Detail->SetNumberField(TEXT("tangent_y_length_squared"),Y.SizeSquared());
                    Detail->SetNumberField(TEXT("normal_length_squared"),Normal.SizeSquared());
                    Detail->SetNumberField(TEXT("orthogonality_error"),Orthogonality);
                    Detail->SetBoolField(TEXT("has_nondegenerate_incident_uv_geometry"),ValidUVGeometry[RenderIndex]);
                    Detail->SetNumberField(TEXT("geometry_normal_squared_tolerance"),GeometryNormalSquaredTolerance);
                    Detail->SetNumberField(TEXT("uv_determinant_absolute_tolerance"),UVDeterminantTolerance);
                    Detail->SetStringField(TEXT("tangent_build_method"),Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.TangentBuildMethod")));
                    Detail->SetStringField(TEXT("mesh_description_mikk_zero_tangents"),Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.MeshDescriptionMikkZeroTangents")));
                    TArray<TSharedPtr<FJsonValue>> Incidents;
                    int32 IncidentCount=0;
                    for(uint32 Triangle=0;Triangle<Section.NumTriangles;++Triangle)
                    {
                        const int32 Offset=int32(Section.BaseIndex+Triangle*3);
                        const uint32 A=LOD.IndexBuffer[Offset],B=LOD.IndexBuffer[Offset+1],C=LOD.IndexBuffer[Offset+2];
                        if(A!=uint32(RenderIndex) && B!=uint32(RenderIndex) && C!=uint32(RenderIndex)) continue;
                        ++IncidentCount;
                        if(Incidents.Num()>=24) continue;
                        const FSoftSkinVertex& VA=Section.SoftVertices[int32(A)-int32(Section.BaseVertexIndex)];
                        const FSoftSkinVertex& VB=Section.SoftVertices[int32(B)-int32(Section.BaseVertexIndex)];
                        const FSoftSkinVertex& VC=Section.SoftVertices[int32(C)-int32(Section.BaseVertexIndex)];
                        const FVector2f U=VB.UVs[0]-VA.UVs[0],V=VC.UVs[0]-VA.UVs[0];
                        const FVector3f E1=VB.Position-VA.Position,E2=VC.Position-VA.Position;
                        const FVector3f Cross=FVector3f::CrossProduct(E1,E2);
                        const float Det=U.X*V.Y-U.Y*V.X;
                        const FVector3f RawTangent=FMath::Abs(Det)>1.e-20f?(E1*V.Y-E2*U.Y)/Det:FVector3f::ZeroVector;
                        const FVector3f Projected=RawTangent-Normal*FVector3f::DotProduct(RawTangent,Normal);
                        const TSharedRef<FJsonObject> Incident=MakeShared<FJsonObject>();
                        Incident->SetNumberField(TEXT("section_triangle"),Triangle);
                        Incident->SetNumberField(TEXT("uv_determinant"),Det);
                        Incident->SetNumberField(TEXT("geometry_cross_length_squared"),Cross.SizeSquared());
                        Incident->SetNumberField(TEXT("geometric_normal_dot_source_normal"),FVector3f::DotProduct(Cross.GetSafeNormal(),Normal));
                        Incident->SetNumberField(TEXT("raw_tangent_length_squared"),RawTangent.SizeSquared());
                        Incident->SetNumberField(TEXT("normal_projected_raw_tangent_length_squared"),Projected.SizeSquared());
                        Incident->SetStringField(TEXT("raw_tangent"),RawTangent.ToString());
                        Incident->SetStringField(TEXT("position_a"),VA.Position.ToString());
                        Incident->SetStringField(TEXT("position_b"),VB.Position.ToString());
                        Incident->SetStringField(TEXT("position_c"),VC.Position.ToString());
                        Incident->SetStringField(TEXT("uv_a"),VA.UVs[0].ToString());
                        Incident->SetStringField(TEXT("uv_b"),VB.UVs[0].ToString());
                        Incident->SetStringField(TEXT("uv_c"),VC.UVs[0].ToString());
                        Incidents.Add(MakeShared<FJsonValueObject>(Incident));
                    }
                    Detail->SetNumberField(TEXT("incident_triangle_count"),IncidentCount);
                    Detail->SetArrayField(TEXT("incident_triangles_first_24"),Incidents);
                    Why=TEXT("Built tangent frame failed; diagnostic=")+Json(Detail);
                    return false;
                }
            }
            int32 Count=0;
            for(int32 Influence=0;Influence<MAX_TOTAL_INFLUENCES;++Influence)
            {
                const uint16 RawWeight=Vertex.InfluenceWeights[Influence];
                if(RawWeight==0) continue;
                const int32 LocalBone=Vertex.InfluenceBones[Influence];
                if(!Section.BoneMap.IsValidIndex(LocalBone)) { Why=TEXT("Built skin bone map is invalid");return false; }
                const int32 Bone=Section.BoneMap[LocalBone];
                const auto* Expected=Source.Weights[Point].FindByPredicate([&](const auto& Item) { return Item.BoneIndex==Bone; });
                if(!Expected || FMath::Abs(double(Expected->Weight)-double(RawWeight)/65535.0)>WeightTolerance)
                { Why=TEXT("Built skin influence differs beyond 16-bit quantization tolerance");return false; }
                ++Count;++RenderWeights;
            }
            if(Count!=Source.Weights[Point].Num()) { Why=TEXT("Built LOD dropped source skin influences");return false; }
            ++RenderVertices;
        }
    }
    if(RenderTriangles!=Source.Data.Faces.Num()) { Why=TEXT("Built LOD triangle count differs");return false; }
    Report->SetStringField(TEXT("status"),TEXT("passed"));
    Report->SetStringField(TEXT("mesh"),Mesh->GetPathName());
    Report->SetStringField(TEXT("skeleton"),Mesh->GetSkeleton()->GetPathName());
    Report->SetStringField(TEXT("source_sha256"),Source.Hash);
    Report->SetNumberField(TEXT("points"),Source.Data.Points.Num());
    Report->SetNumberField(TEXT("triangles"),Source.Data.Faces.Num());
    Report->SetNumberField(TEXT("bones"),Ref.GetRawBoneNum());
    Report->SetNumberField(TEXT("uv_channels"),Source.Data.NumTexCoords);
    Report->SetBoolField(TEXT("vertex_colors"),Source.Data.bHasVertexColors);
    Report->SetNumberField(TEXT("source_weight_rows_checked"),double(VerifiedWeights));
    Report->SetNumberField(TEXT("built_vertices_checked"),double(RenderVertices));
    Report->SetNumberField(TEXT("built_weight_rows_checked"),double(RenderWeights));
    Report->SetNumberField(TEXT("maximum_influences"),Source.MaximumInfluences);
    Report->SetNumberField(TEXT("weight_absolute_tolerance"),WeightTolerance);
    Report->SetNumberField(TEXT("built_tangent_frames_checked"),double(TangentFramesChecked));
    Report->SetNumberField(TEXT("built_tangent_frames_invalid"),double(BadTangentFrames));
    Report->SetNumberField(TEXT("degenerate_uv_or_geometry_tangent_exceptions"),double(DegenerateFrameExceptions));
    Report->SetNumberField(TEXT("near_degenerate_geometry_tangent_exceptions"),double(NearDegenerateGeometryFrameExceptions));
    Report->SetNumberField(TEXT("geometry_normal_squared_tolerance"),GeometryNormalSquaredTolerance);
    Report->SetNumberField(TEXT("uv_determinant_absolute_tolerance"),UVDeterminantTolerance);
    Report->SetStringField(TEXT("tangent_degeneracy_classification"),TEXT("Stable incident geometry requires a finite triangle cross-product squared length >= UE_SMALL_NUMBER, matching UE GetSafeNormal, and a finite absolute UV determinant > uv_determinant_absolute_tolerance. This is UE safe-normal geometry classification, not MikkTSpace's internal degeneracy threshold. Near-degenerate exceptions have a nonzero cross-product squared length below UE_SMALL_NUMBER and no stable incident geometry/UV triangle. All tangent vectors must remain finite."));
    Report->SetNumberField(TEXT("maximum_built_source_normal_component_error"),MaximumNormalError);
    Report->SetNumberField(TEXT("maximum_built_normal_length_squared_error"),MaximumNormalLengthError);
    Report->SetNumberField(TEXT("maximum_built_tangent_normal_dot"),MaximumTangentNormalDot);
    Report->SetNumberField(TEXT("normal_error_max"),MaximumNormalError);
    Report->SetNumberField(TEXT("tangent_orthogonality_error_max"),MaximumTangentNormalDot);
    Report->SetNumberField(TEXT("degenerate_count"),double(DegenerateFrameExceptions));
    Report->SetBoolField(TEXT("built_tangent_frames_verified"),CheckBuiltTangents && BadTangentFrames==0);
    Report->SetStringField(TEXT("source_corner_layout"),Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.CornerLayout")));
    Report->SetNumberField(TEXT("corner_layout_version"),Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.CornerLayout"))==TEXT("2")?2:1);
    Report->SetStringField(TEXT("tangent_build_method"),Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.TangentBuildMethod")));
    Report->SetNumberField(TEXT("mesh_description_corner_wedge_mismatches"),FCString::Atoi64(*Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.MeshDescriptionCornerWedgeMismatches"))));
    Report->SetNumberField(TEXT("mesh_description_mikk_zero_tangents"),FCString::Atoi64(*Mesh->GetPackage()->GetMetaData().GetValue(Mesh,TEXT("PCAssetImporter.MeshDescriptionMikkZeroTangents"))));
    Report->SetStringField(TEXT("scope"),TEXT("Saved source geometry, triangle winding/material indices, UVs, normals, colors, bind skeleton, every built nonzero skin influence, normalized built source normals, finite normalized tangent frames orthogonal to those normals. Tangent failures are excepted only for render vertices belonging exclusively to degenerate geometry/UV triangles. Original bone scales and tangent vectors were not present in ActorX. Animation, cloth simulation and visual appearance require separate validation."));
    return true;
}

static bool BuildCanonicalCornerLayout(const FActorX& Source,USkeletalMesh* Mesh,const TArray<FString>& ImportNames,FString& Why)
{
    FSkeletalMeshImportData BuildData=Source.Data;
    for(int32 I=0;I<ImportNames.Num();++I) BuildData.Materials[I].MaterialImportName=ImportNames[I];
    // The legacy skeletal Mikk callback writes tangent arrays at face*3+corner,
    // while its render builder reads Face.WedgeIndex. Canonical unique corners
    // make both indexing conventions identical without duplicating points,
    // changing source skinning, or changing any corner attributes.
    TArray<SkeletalMeshImportData::FVertex> Corners;
    Corners.Reserve(BuildData.Faces.Num()*3);
    for(auto& Face:BuildData.Faces)
        for(int32 Corner=0;Corner<3;++Corner)
        {
            const auto Wedge=BuildData.Wedges[Face.WedgeIndex[Corner]];
            Face.WedgeIndex[Corner]=Corners.Add(Wedge);
        }
    BuildData.Wedges=MoveTemp(Corners);
    BuildData.bHasTangents=false;
    FSkinnedAssetCompilingManager::Get().FinishCompilation({Mesh});
    Mesh->PreEditChange(nullptr);
    Mesh->InvalidateDeriveDataCacheGUID();
    FSkeletalMeshLODInfo* Info=Mesh->GetLODInfo(0);
    if(!Info || !Mesh->GetImportedModel() || Mesh->GetImportedModel()->LODModels.Num()!=1)
    { Why=TEXT("Canonical rebuild requires exactly one existing imported LOD");return false; }
    Info->BuildSettings.bRecomputeNormals=false;
    Info->BuildSettings.bRecomputeTangents=false;
    Info->BuildSettings.bUseMikkTSpace=true;
    Info->BuildSettings.bRemoveDegenerates=false;
    Info->BuildSettings.bUseFullPrecisionUVs=true;
    Info->BuildSettings.bUseHighPrecisionTangentBasis=true;
    Info->BuildSettings.bUseHighPrecisionSkinWeights=true;
    Info->BuildSettings.BoneInfluenceLimit=8;
    Info->BuildSettings.ThresholdPosition=0.f;
    Info->BuildSettings.ThresholdTangentNormal=0.00002f;
    Info->BuildSettings.ThresholdUV=0.f;
    Mesh->ClearMeshDescriptionAndBulkData(0);
    Mesh->GetImportedModel()->LODModels[0].Empty();
    Mesh->GetImportedModel()->LODModels[0].NumTexCoords=BuildData.NumTexCoords;
    PRAGMA_DISABLE_DEPRECATION_WARNINGS
    Mesh->SaveLODImportedData(0,BuildData);
    PRAGMA_ENABLE_DEPRECATION_WARNINGS
    if(!Mesh->HasMeshDescription(0)) { Why=TEXT("Canonical ActorX to MeshDescription conversion failed");return false; }
    FMeshDescription* Description=Mesh->GetMeshDescription(0);
    if(!Description) { Why=TEXT("Canonical MeshDescription is unavailable");return false; }
    // The public MeshDescription callback addresses tangent output by the
    // actual triangle's vertex-instance IDs and explicitly initializes the
    // ignore-degenerates flag. It reads, but never rewrites, source normals.
    FStaticMeshOperations::ComputeMikktTangents(*Description,false);
    const FSkeletalMeshConstAttributes Attributes(*Description);
    const auto Normals=Attributes.GetVertexInstanceNormals();
    const auto Tangents=Attributes.GetVertexInstanceTangents();
    int64 ZeroTangents=0;
    for(const FVertexInstanceID Instance:Description->VertexInstances().GetElementIDs())
    {
        const int32 Point=Description->GetVertexInstanceVertex(Instance).GetValue();
        if(!Source.Normals.IsValidIndex(Point) || !Normals[Instance].Equals(Source.Normals[Point],0.000001f))
        { Why=TEXT("MeshDescription tangent generation changed a source normal");return false; }
        if(Tangents[Instance].ContainsNaN()) { Why=TEXT("MeshDescription MikkTSpace returned a nonfinite tangent");return false; }
        if(Tangents[Instance].IsNearlyZero()) ++ZeroTangents;
    }
    const FSkeletalMeshImportData RoundTrip=FSkeletalMeshImportData::CreateFromMeshDescription(*Description);
    int64 CornerMismatches=0;
    for(int32 Face=0;Face<RoundTrip.Faces.Num();++Face)
        for(int32 Corner=0;Corner<3;++Corner)
            if(RoundTrip.Faces[Face].WedgeIndex[Corner]!=uint32(3*Face+Corner)) ++CornerMismatches;
    if(!Mesh->CommitMeshDescription(0)) { Why=TEXT("Committing generated MeshDescription tangents failed");return false; }
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.CornerLayout"),TEXT("2"));
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.TangentNormalThreshold"),TEXT("0.00002"));
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.TangentBuildMethod"),TEXT("MeshDescriptionMikkTSpace"));
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.MeshDescriptionCornerWedgeMismatches"),*LexToString(CornerMismatches));
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.MeshDescriptionMikkZeroTangents"),*LexToString(ZeroTangents));
    IMeshBuilderModule& Builder=IMeshBuilderModule::GetForRunningPlatform();
    Mesh->AllocateResourceForRendering();
    const FSkeletalMeshBuildParameters Parameters(Mesh,GetTargetPlatformManagerRef().GetRunningTargetPlatform(),0,false);
    const bool Built=Builder.BuildSkeletalMesh(*Mesh->GetResourceForRendering(),Parameters);
    Mesh->ReleaseResources();
    if(!Built) { Why=TEXT("Canonical skeletal mesh build failed");return false; }
    Mesh->PostEditChange();
    FSkinnedAssetCompilingManager::Get().FinishCompilation({Mesh});
    return true;
}
} // namespace PCActorX

FString UPCAssetImportLibrary::VerifyActorx(const FString& SourceFile,const FString& MeshPackage)
{
    using namespace PCActorX;
    if(!TargetAllowed(MeshPackage)) return FailureJson(TEXT("Target must be a package below /Game/PCAssets/"));
    FActorX Source;FString Why;
    if(!Parse(SourceFile,Source,Why)) return FailureJson(Why);
    USkeletalMesh* Mesh=LoadObject<USkeletalMesh>(nullptr,*(MeshPackage+TEXT(".")+FPackageName::GetLongPackageAssetName(MeshPackage)));
    const TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
    if(!Verify(Source,Mesh,Report,Why)) return FailureJson(Why);
    return Json(Report);
}

FString UPCAssetImportLibrary::ImportActorx(const FString& SourceFile,const FString& TargetPackage,const FString& OptionsJson)
{
    using namespace PCActorX;
    if(!IsInGameThread()) return FailureJson(TEXT("Import must run on the editor game thread"));
    if(!TargetAllowed(TargetPackage)) return FailureJson(TEXT("Target must be a package below /Game/PCAssets/"));
    FActorX Source;FString Why;
    if(!Parse(SourceFile,Source,Why)) return FailureJson(Why);
    TSharedPtr<FJsonObject> Options;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(OptionsJson.IsEmpty()?TEXT("{}"):OptionsJson),Options) || !Options.IsValid())
        return FailureJson(TEXT("OptionsJson must be a JSON object"));
    FString ClaimedHash,SourcePackage;
    Options->TryGetStringField(TEXT("source_sha256"),ClaimedHash);
    Options->TryGetStringField(TEXT("source_package"),SourcePackage);
    if(!ClaimedHash.IsEmpty() && !ClaimedHash.Equals(Source.Hash,ESearchCase::IgnoreCase))
        return FailureJson(TEXT("Options source_sha256 does not match ActorX bytes"));

    TArray<FString> DisplayNames,ImportNames;
    if(!ResolveMaterialNames(Source,Options,DisplayNames,ImportNames,Why)) return FailureJson(Why);
    bool RebuildCorners=false;
    if(Options->HasField(TEXT("rebuild_source_corner_layout")) && !Options->TryGetBoolField(TEXT("rebuild_source_corner_layout"),RebuildCorners))
        return FailureJson(TEXT("rebuild_source_corner_layout must be a JSON bool"));

    const FString AssetName=FPackageName::GetLongPackageAssetName(TargetPackage);
    const FString ObjectPath=TargetPackage+TEXT(".")+AssetName;
    const FString SkeletonPackage=TargetPackage+TEXT("_Skeleton");
    const FString SkeletonName=AssetName+TEXT("_Skeleton");
    const bool ExistingOnDisk=FPackageName::DoesPackageExist(TargetPackage);
    UObject* Existing=FindObject<UObject>(nullptr,*ObjectPath);
    if(!Existing && ExistingOnDisk) Existing=LoadObject<UObject>(nullptr,*ObjectPath);
    if(Existing)
    {
        if(!Owned(Existing)) return FailureJson(TEXT("Refusing to overwrite an asset not created by this importer: ")+ObjectPath);
        if(!Cast<USkeletalMesh>(Existing)) return FailureJson(TEXT("Existing importer asset is not a SkeletalMesh"));
        if(Existing->GetPackage()->GetMetaData().GetValue(Existing,HashKey)!=Source.Hash)
            return FailureJson(TEXT("Source changed for an existing mesh; use a new target package"));
        if(RebuildCorners)
        {
            if(!ExistingOnDisk) return FailureJson(TEXT("Canonical rebuild requires an existing saved mesh and an external backup"));
            USkeletalMesh* Mesh=CastChecked<USkeletalMesh>(Existing);
            const TSharedRef<FJsonObject> Before=MakeShared<FJsonObject>();
            if(!Verify(Source,Mesh,Before,Why,Options,false)) return FailureJson(TEXT("Existing mesh source verification failed before rebuild: ")+Why);
            USkeleton* OriginalSkeleton=Mesh->GetSkeleton();
            const TArray<FSkeletalMaterial> OriginalMaterials=Mesh->GetMaterials();
            if(!BuildCanonicalCornerLayout(Source,Mesh,ImportNames,Why)) return FailureJson(Why);
            if(Mesh->GetSkeleton()!=OriginalSkeleton || Mesh->GetMaterials().Num()!=OriginalMaterials.Num())
                return FailureJson(TEXT("Canonical rebuild changed the existing Skeleton/material slot count"));
            for(int32 I=0;I<OriginalMaterials.Num();++I)
                if(Mesh->GetMaterials()[I].MaterialInterface!=OriginalMaterials[I].MaterialInterface ||
                   Mesh->GetMaterials()[I].MaterialSlotName!=OriginalMaterials[I].MaterialSlotName ||
                   Mesh->GetMaterials()[I].ImportedMaterialSlotName!=OriginalMaterials[I].ImportedMaterialSlotName)
                    return FailureJson(TEXT("Canonical rebuild changed a material binding or slot name"));
            const TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
            if(!Verify(Source,Mesh,Report,Why,Options)) return FailureJson(Why);
            Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.OptionsJSON"),*OptionsJson);
            if(!Save(Mesh)) return FailureJson(TEXT("Saving verified canonical mesh failed"));
            Report->SetStringField(TEXT("operation"),TEXT("rebuilt_source_corner_layout_and_saved"));
            Report->SetNumberField(TEXT("built_vertices_before"),Before->GetNumberField(TEXT("built_vertices_checked")));
            Report->SetNumberField(TEXT("built_vertices_after"),Report->GetNumberField(TEXT("built_vertices_checked")));
            Report->SetObjectField(TEXT("before_rebuild"),Before);
            Report->SetBoolField(TEXT("skeleton_and_material_bindings_preserved"),true);
            return Json(Report);
        }
        const TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
        if(!Verify(Source,Cast<USkeletalMesh>(Existing),Report,Why,Options)) return FailureJson(Why);
        if(!ExistingOnDisk)
            return FailureJson(TEXT("Verified mesh exists only in memory; close the editor without saving and retry in a fresh process"));
        Report->SetStringField(TEXT("operation"),TEXT("verified_existing"));
        return Json(Report);
    }
    if(ExistingOnDisk || PackageHasUnpublishedContent(TargetPackage))
        return FailureJson(TEXT("Target package already exists without the expected mesh; refusing overwrite"));
    if(FPackageName::DoesPackageExist(SkeletonPackage) || PackageHasUnpublishedContent(SkeletonPackage))
        return FailureJson(TEXT("Independent Skeleton package already exists; refusing unverified reuse"));

    UPackage* MeshOuter=CreatePackage(*TargetPackage);
    UPackage* SkeletonOuter=CreatePackage(*SkeletonPackage);
    USkeletalMesh* Mesh=NewObject<USkeletalMesh>(MeshOuter,*AssetName,RF_Public|RF_Standalone|RF_Transactional);
    USkeleton* Skeleton=NewObject<USkeleton>(SkeletonOuter,*SkeletonName,RF_Public|RF_Standalone|RF_Transactional);
    if(!Mesh || !Skeleton) return FailureJson(TEXT("Could not allocate skeletal assets"));
    Mesh->PreEditChange(nullptr);
    Mesh->InvalidateDeriveDataCacheGUID();
    Mesh->GetImportedModel()->LODModels.Add(new FSkeletalMeshLODModel());
    FSkeletalMeshLODInfo& Info=Mesh->AddLODInfo();
    Info.ReductionSettings.NumOfTrianglesPercentage=1.f;
    Info.ReductionSettings.NumOfVertPercentage=1.f;
    Info.ReductionSettings.MaxDeviationPercentage=0.f;
    Info.LODHysteresis=0.02f;
    Info.BuildSettings.bRecomputeNormals=false;
    Info.BuildSettings.bRecomputeTangents=true;
    Info.BuildSettings.bUseMikkTSpace=true;
    Info.BuildSettings.bRemoveDegenerates=false;
    Info.BuildSettings.bUseFullPrecisionUVs=true;
    Info.BuildSettings.bUseHighPrecisionTangentBasis=true;
    Info.BuildSettings.bUseHighPrecisionSkinWeights=true;
    Info.BuildSettings.BoneInfluenceLimit=8;
    Info.BuildSettings.ThresholdPosition=0.f;
    Info.BuildSettings.ThresholdTangentNormal=0.00002f;
    Info.BuildSettings.ThresholdUV=0.f;
    Mesh->GetImportedModel()->LODModels[0].NumTexCoords=Source.Data.NumTexCoords;
    for(int32 I=0;I<Source.Data.Materials.Num();++I)
        Mesh->GetMaterials().Add(FSkeletalMaterial(nullptr,true,false,FName(*DisplayNames[I]),FName(*ImportNames[I])));

    {
        FReferenceSkeletonModifier Modifier(Mesh->GetRefSkeleton(),Skeleton);
        for(const auto& Bone:Source.Data.RefBonesBinary)
            Modifier.Add(FMeshBoneInfo(FName(*Bone.Name),Bone.Name,Bone.ParentIndex),FTransform(Bone.BonePos.Transform));
    }
    FBox Bounds(ForceInit);
    for(const FVector3f& Point:Source.Data.Points) Bounds+=FVector(Point);
    Mesh->SetImportedBounds(FBoxSphereBounds(Bounds));
    Mesh->SetHasVertexColors(Source.Data.bHasVertexColors);
    Mesh->SetVertexColorGuid(Source.Data.bHasVertexColors?FGuid::NewGuid():FGuid());
    Mesh->CalculateInvRefMatrices();
    if(!Skeleton->MergeAllBonesToBoneTree(Mesh)) return FailureJson(TEXT("Independent Skeleton bone-tree creation failed"));
    Mesh->SetSkeleton(Skeleton);
    UAssetImportData* ImportData=NewObject<UAssetImportData>(Mesh,TEXT("AssetImportData"));
    ImportData->Update(Source.Filename);
    Mesh->SetAssetImportData(ImportData);
    Tag(Mesh,Source,SourcePackage);Tag(Skeleton,Source,SourcePackage);
    Mesh->GetPackage()->GetMetaData().SetValue(Mesh,TEXT("PCAssetImporter.OptionsJSON"),*OptionsJson);

    if(!BuildCanonicalCornerLayout(Source,Mesh,ImportNames,Why)) return FailureJson(Why);
    const TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
    if(!Verify(Source,Mesh,Report,Why,Options)) return FailureJson(Why);
    FAssetRegistryModule::AssetCreated(Skeleton);FAssetRegistryModule::AssetCreated(Mesh);
    if(!Save(Skeleton) || !Save(Mesh)) return FailureJson(TEXT("Saving verified skeletal assets failed"));
    Report->SetStringField(TEXT("operation"),TEXT("imported_and_saved"));
    return Json(Report);
}
