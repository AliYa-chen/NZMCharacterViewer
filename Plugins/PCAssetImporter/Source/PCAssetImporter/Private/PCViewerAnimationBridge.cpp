#include "PCViewerAnimationLibrary.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimData/IAnimationDataController.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Components/SkeletalMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "MovieScene.h"
#include "MovieSceneSequence.h"
#include "MovieSceneTrack.h"
#include "ReferenceSkeleton.h"
#include "Rigs/FKControlRig.h"
#include "Sections/MovieSceneParameterSection.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/MetaData.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/StrongObjectPtr.h"

namespace PCViewerAnimation
{
constexpr const TCHAR* OwnerTag=TEXT("PCViewerAnimation.Owner");
constexpr const TCHAR* Owner=TEXT("pc_viewer_animation_v1");
constexpr const TCHAR* SourceHashTag=TEXT("PCViewerAnimation.SourcePackageSHA1");
constexpr const TCHAR* MeshHashTag=TEXT("PCViewerAnimation.TargetMeshPackageSHA1");
constexpr double RawPositionTolerance=0.02;
constexpr double RawRotationToleranceDegrees=0.03;
constexpr double CompressedPositionTolerance=0.35;
constexpr double CompressedRotationToleranceDegrees=0.5;

static FString Encode(const TSharedRef<FJsonObject>& Value)
{
    FString Text;
    FJsonSerializer::Serialize(Value,TJsonWriterFactory<>::Create(&Text));
    return Text;
}

static FString Failure(const FString& Why)
{
    const auto Report=MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"),TEXT("failed"));
    Report->SetStringField(TEXT("error"),Why);
    UE_LOG(LogTemp,Error,TEXT("PCViewerAnimation: %s"),*Why);
    return Encode(Report);
}

static bool ValidPackage(const FString& Name,const FString& Prefix)
{
    return Name.StartsWith(Prefix) && !Name.Contains(TEXT(".")) && FPackageName::IsValidLongPackageName(Name);
}

static FString ObjectPath(const FString& Package)
{
    return Package+TEXT(".")+FPackageName::GetLongPackageAssetName(Package);
}

static FString PackageSHA1(const FString& Package)
{
    TArray<uint8> Bytes;
    const FString File=FPackageName::LongPackageNameToFilename(Package,FPackageName::GetAssetPackageExtension());
    if(!FFileHelper::LoadFileToArray(Bytes,*File)) return FString();
    uint8 Hash[FSHA1::DigestSize];
    FSHA1::HashBuffer(Bytes.GetData(),Bytes.Num(),Hash);
    return BytesToHex(Hash,FSHA1::DigestSize).ToLower();
}

static bool Save(UObject* Asset)
{
    const FString File=FPackageName::LongPackageNameToFilename(Asset->GetPackage()->GetName(),FPackageName::GetAssetPackageExtension());
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(File),true);
    FSavePackageArgs Args;
    Args.TopLevelFlags=RF_Public|RF_Standalone;
    Args.SaveFlags=SAVE_NoError;
    return UPackage::SavePackage(Asset->GetPackage(),Asset,*File,Args);
}

static double RotationError(const FQuat& A,const FQuat& B)
{
    // Scalar normalization avoids SIMD reciprocal-square-root error becoming
    // a false angular error through acos near one. The chord formulation also
    // stays well conditioned for the very small raw float-key differences.
    const double ANorm=FMath::Sqrt(A.X*A.X+A.Y*A.Y+A.Z*A.Z+A.W*A.W);
    const double BNorm=FMath::Sqrt(B.X*B.X+B.Y*B.Y+B.Z*B.Z+B.W*B.W);
    if(ANorm<UE_SMALL_NUMBER || BNorm<UE_SMALL_NUMBER) return 180.0;
    const double AX=A.X/ANorm,AY=A.Y/ANorm,AZ=A.Z/ANorm,AW=A.W/ANorm;
    double BX=B.X/BNorm,BY=B.Y/BNorm,BZ=B.Z/BNorm,BW=B.W/BNorm;
    if(AX*BX+AY*BY+AZ*BZ+AW*BW<0) { BX=-BX;BY=-BY;BZ=-BZ;BW=-BW; }
    const double Difference=FMath::Sqrt(FMath::Square(AX-BX)+FMath::Square(AY-BY)+FMath::Square(AZ-BZ)+FMath::Square(AW-BW));
    const double Sum=FMath::Sqrt(FMath::Square(AX+BX)+FMath::Square(AY+BY)+FMath::Square(AZ+BZ)+FMath::Square(AW+BW));
    return FMath::RadiansToDegrees(4.0*FMath::Atan2(Difference,Sum));
}

static FVector AccurateEuler(const FQuat4f& Rotation)
{
    const FQuat Q(Rotation);
    const double Norm=FMath::Sqrt(Q.X*Q.X+Q.Y*Q.Y+Q.Z*Q.Z+Q.W*Q.W);
    const double X=Q.X/Norm,Y=Q.Y/Norm,Z=Q.Z/Norm,W=Q.W/Norm;
    const double SinePitch=FMath::Clamp(2.0*(Z*X-W*Y),-1.0,1.0);
    // UE's generic quaternion-to-Euler conversion snaps an entire .1-degree
    // neighbourhood of gimbal lock. Preserve those genuine small IK rotations.
    if(FMath::Abs(SinePitch)>1.0-1.e-12)
        return FVector(0.0,FMath::Sign(SinePitch)*90.0,
            FMath::RadiansToDegrees(FMath::Sign(SinePitch)*2.0*FMath::Atan2(X,W)));
    return FVector(
        FMath::RadiansToDegrees(FMath::Atan2(-2.0*(W*X+Y*Z),1.0-2.0*(X*X+Y*Y))),
        FMath::RadiansToDegrees(FMath::Asin(SinePitch)),
        FMath::RadiansToDegrees(FMath::Atan2(2.0*(W*Z+X*Y),1.0-2.0*(Y*Y+Z*Z))));
}

static bool CorrectSingularEulerKeys(IAnimationDataController& Controller,const TArray<FName>& BoneNames,
    const TArray<TArray<FQuat4f>>& Rotations,int32& CorrectedTracks,FString& Why)
{
    UMovieSceneSequence* Sequence=Cast<UMovieSceneSequence>(Controller.GetModelInterface().GetObject());
    if(!Sequence) return true; // Legacy quaternion DataModels need no Euler correction.
    UMovieScene* MovieScene=Sequence->GetMovieScene();
    if(!MovieScene) { Why=TEXT("Sequencer animation DataModel has no MovieScene");return false; }
    auto MutableModel=Controller.GetModelInterface();
    IAnimationDataModel::FEvaluationAndModificationLock Lock(*MutableModel);
    TMap<FName,FTransformParameterNameAndCurves*> Parameters;
    for(UMovieSceneTrack* Track:MovieScene->GetTracks())
        for(UMovieSceneSection* Section:Track->GetAllSections())
            if(UMovieSceneParameterSection* ParameterSection=Cast<UMovieSceneParameterSection>(Section))
                for(auto& Parameter:ParameterSection->GetTransformParameterNamesAndCurves()) Parameters.Add(Parameter.ParameterName,&Parameter);
    for(int32 Bone=0;Bone<BoneNames.Num();++Bone)
    {
        bool NeedsCorrection=false;
        for(const FQuat4f& Q:Rotations[Bone])
            if(RotationError(FQuat(Q),FQuat::MakeFromEuler(FVector(Q.Euler())))>RawRotationToleranceDegrees/4.0)
            { NeedsCorrection=true;break; }
        if(!NeedsCorrection) continue;
        const FName ControlName=UFKControlRig::GetControlName(BoneNames[Bone],ERigElementType::Bone);
        FTransformParameterNameAndCurves** Found=Parameters.Find(ControlName);
        if(!Found) { Why=TEXT("Could not find public Euler rotation channels for ")+BoneNames[Bone].ToString();return false; }
        TArray<FFrameNumber> Frames;
        TArray<FMovieSceneFloatValue> Values[3];
        FVector Previous=FVector::ZeroVector;
        for(int32 Frame=0;Frame<Rotations[Bone].Num();++Frame)
        {
            Frames.Add(FFrameNumber(Frame));
            FVector Euler=AccurateEuler(Rotations[Bone][Frame]);
            for(int32 Axis=0;Axis<3;++Axis)
            {
                if(Frame) FMath::WindRelativeAnglesDegrees(Previous[Axis],Euler[Axis]);
                FMovieSceneFloatValue& Key=Values[Axis].AddDefaulted_GetRef();
                Key.Value=float(Euler[Axis]);Key.InterpMode=ERichCurveInterpMode::RCIM_Linear;
            }
            Previous=Euler;
        }
        for(int32 Axis=0;Axis<3;++Axis)
        {
            (*Found)->Rotation[Axis].Reset();
            (*Found)->Rotation[Axis].SetKeysOnly(Frames,Values[Axis]);
        }
        ++CorrectedTracks;
    }
    return true;
}

struct FInputs
{
    TStrongObjectPtr<UAnimSequence> Source;
    TStrongObjectPtr<USkeletalMesh> Mesh;
    FString SourceHash,MeshHash;
    FFrameRate FrameRate;
    int32 Frames=0,Keys=0;
    TArray<FName> SourceNames,TargetNames;
    TArray<int32> SourceParents,TargetParents,TargetToSource;
    TArray<FTransform> SourceReference,TargetReference,SourceGlobalReference,TargetGlobalReference,Corrections;
    TArray<TArray<FTransform>> SourceKeys;
    TArray<FName> UnmatchedTargetBones,MatchedCoreBones;
    int32 Matched=0,MatchedTracked=0,ParentNameDifferences=0;
    bool HumanoidCompatible=false,ExactAccessoryCompatible=false;
    double MaxReferencePositionDifference=0;
};

static bool Prepare(const FString& SourcePackage,const FString& MeshPackage,FInputs& Out,FString& Why)
{
    if(!IsInGameThread()) { Why=TEXT("Must run on editor game thread");return false; }
    if(!ValidPackage(SourcePackage,TEXT("/Game/Characters/")) || !SourcePackage.Contains(TEXT("Idle")))
    { Why=TEXT("Source must be an existing viewer idle animation below /Game/Characters/");return false; }
    if(!ValidPackage(MeshPackage,TEXT("/Game/PCAssets/Meshes/")))
    { Why=TEXT("Target mesh must be below /Game/PCAssets/Meshes/");return false; }
    if(!FPackageName::DoesPackageExist(SourcePackage) || !FPackageName::DoesPackageExist(MeshPackage))
    { Why=TEXT("Source animation or target mesh package is missing");return false; }
    Out.Source=TStrongObjectPtr<UAnimSequence>(LoadObject<UAnimSequence>(nullptr,*ObjectPath(SourcePackage)));
    Out.Mesh=TStrongObjectPtr<USkeletalMesh>(LoadObject<USkeletalMesh>(nullptr,*ObjectPath(MeshPackage)));
    if(!Out.Source || !Out.Mesh || !Out.Source->GetSkeleton() || !Out.Mesh->GetSkeleton())
    { Why=TEXT("Exact source animation, target mesh or Skeleton failed to load");return false; }
    const IAnimationDataModel* Model=Out.Source->GetDataModel();
    if(!Model || !Model->HasBeenPopulated() || Model->GetNumBoneTracks()==0)
    { Why=TEXT("Source has no editable populated bone DataModel");return false; }
    Out.FrameRate=Model->GetFrameRate();
    Out.Frames=Model->GetNumberOfFrames();
    Out.Keys=Model->GetNumberOfKeys();
    if(Out.Frames<1 || Out.Keys!=Out.Frames+1 || Out.Keys>60000 || !Out.FrameRate.IsValid() || Out.FrameRate.AsDecimal()<=0)
    { Why=TEXT("Source frame rate or dense sample count is invalid");return false; }
    Out.SourceHash=PackageSHA1(SourcePackage);
    Out.MeshHash=PackageSHA1(MeshPackage);
    if(Out.SourceHash.IsEmpty() || Out.MeshHash.IsEmpty())
    { Why=TEXT("Could not fingerprint source or target package");return false; }
    const FReferenceSkeleton& SourceRef=Out.Source->GetSkeleton()->GetReferenceSkeleton();
    const FReferenceSkeleton& TargetRef=Out.Mesh->GetRefSkeleton();
    TMap<FName,int32> SourceIndex;
    for(int32 I=0;I<SourceRef.GetRawBoneNum();++I)
    {
        const FName Name=SourceRef.GetBoneName(I);
        const int32 Parent=SourceRef.GetRawParentIndex(I);
        const FTransform Ref=SourceRef.GetRawRefBonePose()[I];
        if(Parent>=I || Ref.ContainsNaN() || SourceIndex.Contains(Name))
        { Why=TEXT("Invalid source bone hierarchy/reference pose");return false; }
        SourceIndex.Add(Name,I);Out.SourceNames.Add(Name);Out.SourceParents.Add(Parent);Out.SourceReference.Add(Ref);
        Out.SourceGlobalReference.Add(Parent==INDEX_NONE?Ref:Ref*Out.SourceGlobalReference[Parent]);
        auto& Keys=Out.SourceKeys.AddDefaulted_GetRef();
        if(Model->IsValidBoneTrackName(Name))
        {
            Model->GetBoneTrackTransforms(Name,Keys);
            if(Keys.Num()!=Out.Keys) { Why=TEXT("Source DataModel did not return all dense keys for ")+Name.ToString();return false; }
            for(const FTransform& Key:Keys) if(Key.ContainsNaN())
            { Why=TEXT("Source animation has a nonfinite bone key");return false; }
        }
    }
    const TSet<FName> Core={FName(TEXT("b_Root")),FName(TEXT("b_Hips")),FName(TEXT("b_Spine")),
        FName(TEXT("b_Spine1")),FName(TEXT("b_Spine2")),FName(TEXT("b_Head")),FName(TEXT("b_Neck"))};
    for(int32 I=0;I<TargetRef.GetRawBoneNum();++I)
    {
        const FName Name=TargetRef.GetBoneName(I);
        const int32 Parent=TargetRef.GetRawParentIndex(I);
        const FTransform Ref=TargetRef.GetRawRefBonePose()[I];
        if(Parent>=I || Ref.ContainsNaN()) { Why=TEXT("Invalid target bone hierarchy/reference pose");return false; }
        Out.TargetNames.Add(Name);Out.TargetParents.Add(Parent);Out.TargetReference.Add(Ref);
        Out.TargetGlobalReference.Add(Parent==INDEX_NONE?Ref:Ref*Out.TargetGlobalReference[Parent]);
        const int32* Source=SourceIndex.Find(Name);
        Out.TargetToSource.Add(Source?*Source:INDEX_NONE);
        Out.Corrections.Add(Source?Out.TargetGlobalReference[I]*Out.SourceGlobalReference[*Source].Inverse():FTransform::Identity);
        if(Source)
        {
            ++Out.Matched;
            Out.MatchedTracked+=!Out.SourceKeys[*Source].IsEmpty();
            Out.MaxReferencePositionDifference=FMath::Max(Out.MaxReferencePositionDifference,
                FVector::Distance(Out.TargetGlobalReference[I].GetLocation(),Out.SourceGlobalReference[*Source].GetLocation()));
            if(Core.Contains(Name)) Out.MatchedCoreBones.Add(Name);
            const int32 SourceParent=Out.SourceParents[*Source];
            const FName SourceParentName=SourceParent==INDEX_NONE?NAME_None:Out.SourceNames[SourceParent];
            const FName TargetParentName=Parent==INDEX_NONE?NAME_None:Out.TargetNames[Parent];
            Out.ParentNameDifferences+=SourceParentName!=TargetParentName;
        }
        else Out.UnmatchedTargetBones.Add(Name);
    }
    Out.HumanoidCompatible=Out.Matched>=3 && Out.MatchedTracked>=3 && Out.MatchedCoreBones.Num()>=2;
    Out.ExactAccessoryCompatible=!Out.TargetNames.IsEmpty() && Out.Matched==Out.TargetNames.Num() &&
        Out.MatchedTracked==Out.Matched && Out.ParentNameDifferences<=1 && Out.MaxReferencePositionDifference<=0.05;
    if(!Out.HumanoidCompatible && !Out.ExactAccessoryCompatible)
    { Why=TEXT("Insufficient matching animated/core bones for a verified idle adaptation");return false; }
    return true;
}

static TArray<TSharedPtr<FJsonValue>> Names(const TArray<FName>& Values)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    for(FName Name:Values) Result.Add(MakeShared<FJsonValueString>(Name.ToString()));
    return Result;
}

static TSharedRef<FJsonObject> BaseReport(const FInputs& In)
{
    const auto Result=MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("status"),TEXT("passed"));
    Result->SetStringField(TEXT("source_animation"),In.Source->GetPathName());
    Result->SetStringField(TEXT("target_mesh"),In.Mesh->GetPathName());
    Result->SetStringField(TEXT("target_skeleton"),In.Mesh->GetSkeleton()->GetPathName());
    Result->SetStringField(TEXT("source_package_sha1"),In.SourceHash);
    Result->SetStringField(TEXT("target_mesh_package_sha1"),In.MeshHash);
    Result->SetNumberField(TEXT("frame_rate_numerator"),In.FrameRate.Numerator);
    Result->SetNumberField(TEXT("frame_rate_denominator"),In.FrameRate.Denominator);
    Result->SetNumberField(TEXT("frames"),In.Frames);
    Result->SetNumberField(TEXT("keys"),In.Keys);
    Result->SetNumberField(TEXT("duration_seconds"),In.Frames/In.FrameRate.AsDecimal());
    Result->SetNumberField(TEXT("source_bones"),In.SourceNames.Num());
    Result->SetNumberField(TEXT("target_bones"),In.TargetNames.Num());
    Result->SetNumberField(TEXT("matched_bones"),In.Matched);
    Result->SetNumberField(TEXT("matched_source_tracked_bones"),In.MatchedTracked);
    Result->SetNumberField(TEXT("matched_bones_with_different_parent_name"),In.ParentNameDifferences);
    Result->SetArrayField(TEXT("matched_core_bones"),Names(In.MatchedCoreBones));
    Result->SetArrayField(TEXT("unmatched_target_bones_reference_local_follow_parent"),Names(In.UnmatchedTargetBones));
    Result->SetNumberField(TEXT("maximum_reference_position_difference_cm"),In.MaxReferencePositionDifference);
    Result->SetStringField(TEXT("compatibility_basis"),In.HumanoidCompatible?TEXT("matching_animated_humanoid_core"):
        In.ExactAccessoryCompatible?TEXT("complete_animated_accessory_bone_coverage_and_matching_reference_basis"):
        TEXT("insufficient_matching_motion"));
    Result->SetStringField(TEXT("method"),TEXT("existing_idle_DataModel_global_reference_basis_adaptation_v1"));
    Result->SetStringField(TEXT("scope"),TEXT("Existing viewer idle bone motion adapted to independent target bind basis. Missing target bones preserve local reference transforms and follow their target parents. This does not recover missing original animations, curves, notifies, morphs, cloth or material animation."));
    return Result;
}

static void Evaluate(const FInputs& In,int32 Frame,TArray<FTransform>& SourceGlobal,TArray<FTransform>& TargetLocal,TArray<FTransform>& TargetGlobal)
{
    SourceGlobal.SetNum(In.SourceNames.Num());TargetLocal.SetNum(In.TargetNames.Num());TargetGlobal.SetNum(In.TargetNames.Num());
    for(int32 I=0;I<In.SourceNames.Num();++I)
    {
        const FTransform Local=In.SourceKeys[I].IsEmpty()?In.SourceReference[I]:In.SourceKeys[I][Frame];
        const int32 Parent=In.SourceParents[I];
        SourceGlobal[I]=Parent==INDEX_NONE?Local:Local*SourceGlobal[Parent];
    }
    for(int32 I=0;I<In.TargetNames.Num();++I)
    {
        const int32 Parent=In.TargetParents[I],Source=In.TargetToSource[I];
        const FTransform Global=Source!=INDEX_NONE?In.Corrections[I]*SourceGlobal[Source]:
            Parent==INDEX_NONE?In.TargetReference[I]:In.TargetReference[I]*TargetGlobal[Parent];
        FTransform Local=Parent==INDEX_NONE?Global:Global.GetRelativeTransform(TargetGlobal[Parent]);
        Local.NormalizeRotation();
        TargetLocal[I]=Local;
        TargetGlobal[I]=Parent==INDEX_NONE?Local:Local*TargetGlobal[Parent];
    }
}

static bool Verify(const FInputs& In,UAnimSequence* Animation,TSharedRef<FJsonObject> Report,FString& Why)
{
    if(!Animation || Animation->GetSkeleton()!=In.Mesh->GetSkeleton())
    { Why=TEXT("Adapted animation must use the target mesh's exact Skeleton");return false; }
    FMetaData& Meta=Animation->GetPackage()->GetMetaData();
    if(Meta.GetValue(Animation,OwnerTag)!=Owner || Meta.GetValue(Animation,SourceHashTag)!=In.SourceHash ||
       Meta.GetValue(Animation,MeshHashTag)!=In.MeshHash)
    { Why=TEXT("Adapted animation ownership or input package fingerprints differ");return false; }
    const IAnimationDataModel* Model=Animation->GetDataModel();
    if(!Model || Model->GetFrameRate()!=In.FrameRate || Model->GetNumberOfFrames()!=In.Frames ||
       Model->GetNumberOfKeys()!=In.Keys || Model->GetNumBoneTracks()!=In.TargetNames.Num())
    { Why=TEXT("Adapted timing or dense target track coverage differs from source");return false; }
    TArray<TArray<FTransform>> Keys;
    for(FName Name:In.TargetNames)
    {
        if(!Model->IsValidBoneTrackName(Name)) { Why=TEXT("Adapted target bone track missing");return false; }
        auto& Values=Keys.AddDefaulted_GetRef();Model->GetBoneTrackTransforms(Name,Values);
        if(Values.Num()!=In.Keys) { Why=TEXT("Adapted target bone samples are incomplete");return false; }
    }
    TArray<FTransform> SourceGlobal,ExpectedLocal,ExpectedGlobal,ActualGlobal;
    ActualGlobal.SetNum(In.TargetNames.Num());
    double MaxPosition=0,MaxRotation=0,MaxScale=0,MaxMotion=0;
    int32 MaxRotationFrame=0,MaxRotationBone=0;
    FQuat MaxActualRotation,MaxExpectedRotation;
    for(int32 Frame=0;Frame<In.Keys;++Frame)
    {
        Evaluate(In,Frame,SourceGlobal,ExpectedLocal,ExpectedGlobal);
        for(int32 Bone=0;Bone<In.TargetNames.Num();++Bone)
        {
            const FTransform& Local=Keys[Bone][Frame];const int32 Parent=In.TargetParents[Bone];
            if(Local.ContainsNaN()) { Why=TEXT("Adapted raw key is not finite");return false; }
            ActualGlobal[Bone]=Parent==INDEX_NONE?Local:Local*ActualGlobal[Parent];
            MaxPosition=FMath::Max(MaxPosition,FVector::Distance(ActualGlobal[Bone].GetLocation(),ExpectedGlobal[Bone].GetLocation()));
            const double Angle=RotationError(ActualGlobal[Bone].GetRotation(),ExpectedGlobal[Bone].GetRotation());
            if(Angle>MaxRotation)
            {
                MaxRotation=Angle;MaxRotationFrame=Frame;MaxRotationBone=Bone;
                MaxActualRotation=ActualGlobal[Bone].GetRotation();MaxExpectedRotation=ExpectedGlobal[Bone].GetRotation();
            }
            MaxScale=FMath::Max(MaxScale,(ActualGlobal[Bone].GetScale3D()-ExpectedGlobal[Bone].GetScale3D()).GetAbsMax());
            MaxMotion=FMath::Max(MaxMotion,FVector::Distance(ActualGlobal[Bone].GetLocation(),In.TargetGlobalReference[Bone].GetLocation()));
        }
    }
    if(MaxPosition>RawPositionTolerance || MaxRotation>RawRotationToleranceDegrees || MaxScale>0.001)
    {
        Evaluate(In,MaxRotationFrame,SourceGlobal,ExpectedLocal,ExpectedGlobal);
        Why=FString::Printf(TEXT("Raw global adaptation error exceeds tolerance: cm=%g deg=%g scale=%g bone=%s frame=%d actual=%s expected=%s actual_local=%s expected_local=%s"),MaxPosition,MaxRotation,MaxScale,*In.TargetNames[MaxRotationBone].ToString(),MaxRotationFrame,*MaxActualRotation.ToString(),*MaxExpectedRotation.ToString(),*Keys[MaxRotationBone][MaxRotationFrame].ToString(),*ExpectedLocal[MaxRotationBone].ToString());return false;
    }
    Animation->CacheDerivedDataForCurrentPlatform();
    Animation->WaitOnExistingCompression(true);
    if(!Animation->IsBoneCompressedDataValid())
    { Why=TEXT("Adapted animation has no valid runtime compressed bone data");return false; }
    double MaxCompressedPosition=0,MaxCompressedRotation=0;
    for(int32 Frame=0;Frame<In.Keys;++Frame)
    {
        Evaluate(In,Frame,SourceGlobal,ExpectedLocal,ExpectedGlobal);
        const double Time=Frame/In.FrameRate.AsDecimal();
        for(int32 Bone=0;Bone<In.TargetNames.Num();++Bone)
        {
            FTransform Local;
            Animation->GetBoneTransform(Local,FSkeletonPoseBoneIndex(Bone),FAnimExtractContext(Time,false),false);
            const int32 Parent=In.TargetParents[Bone];
            if(Local.ContainsNaN()) { Why=TEXT("Compressed bone key is not finite");return false; }
            ActualGlobal[Bone]=Parent==INDEX_NONE?Local:Local*ActualGlobal[Parent];
            MaxCompressedPosition=FMath::Max(MaxCompressedPosition,FVector::Distance(ActualGlobal[Bone].GetLocation(),ExpectedGlobal[Bone].GetLocation()));
            MaxCompressedRotation=FMath::Max(MaxCompressedRotation,RotationError(ActualGlobal[Bone].GetRotation(),ExpectedGlobal[Bone].GetRotation()));
        }
    }
    if(MaxCompressedPosition>CompressedPositionTolerance || MaxCompressedRotation>CompressedRotationToleranceDegrees)
    { Why=FString::Printf(TEXT("Compressed adaptation differs beyond tolerance: cm=%g deg=%g"),MaxCompressedPosition,MaxCompressedRotation);return false; }
    Report->SetStringField(TEXT("animation"),Animation->GetPathName());
    Report->SetStringField(TEXT("source_kind"),Meta.GetValue(Animation,TEXT("PCViewerAnimation.SourceKind")));
    Report->SetStringField(TEXT("original_animation_recovered"),TEXT("No; existing viewer motion is adapted and missing source-specific actions remain unavailable"));
    Report->SetNumberField(TEXT("raw_global_position_error_max_cm"),MaxPosition);
    Report->SetNumberField(TEXT("raw_global_rotation_error_max_degrees"),MaxRotation);
    Report->SetNumberField(TEXT("raw_global_scale_error_max"),MaxScale);
    Report->SetNumberField(TEXT("compressed_global_position_error_max_cm"),MaxCompressedPosition);
    Report->SetNumberField(TEXT("compressed_global_rotation_error_max_degrees"),MaxCompressedRotation);
    Report->SetNumberField(TEXT("maximum_bone_displacement_from_bind_cm"),MaxMotion);
    Report->SetNumberField(TEXT("raw_bone_keys_checked"),double(In.Keys)*In.TargetNames.Num());
    Report->SetNumberField(TEXT("compressed_samples_checked"),In.Keys);
    Report->SetNumberField(TEXT("compressed_bone_keys_checked"),double(In.Keys)*In.TargetNames.Num());
    Report->SetNumberField(TEXT("corrected_near_singular_euler_tracks"),FCString::Atoi(*Meta.GetValue(Animation,TEXT("PCViewerAnimation.CorrectedEulerTracks"))));
    Report->SetBoolField(TEXT("runtime_compressed_data_valid"),true);
    return true;
}
}

bool UPCViewerAnimationLibrary::SamplePreviewPose(USkeletalMeshComponent* Component,UAnimSequence* Animation,float TimeSeconds)
{
    if(!IsInGameThread() || !Component || !Animation || !FMath::IsFinite(TimeSeconds) ||
       !Component->GetSkeletalMeshAsset() || Component->GetSkeletalMeshAsset()->GetSkeleton()!=Animation->GetSkeleton()) return false;
    Component->SetAnimationMode(EAnimationMode::AnimationSingleNode);
    Component->SetAnimation(Animation);
    Component->SetPosition(FMath::Clamp(TimeSeconds,0.0f,float(Animation->GetPlayLength())),false);
    Component->TickAnimation(0.0f,false);
    Component->RefreshBoneTransforms();
    Component->UpdateComponentToWorld();
    Component->MarkRenderDynamicDataDirty();
    const TArray<FTransform>& Pose=Component->GetComponentSpaceTransforms();
    if(Pose.IsEmpty()) return false;
    for(const FTransform& Bone:Pose) if(Bone.ContainsNaN()) return false;
    return true;
}

FString UPCViewerAnimationLibrary::InspectAnimationCompatibility(const FString& SourceAnimationPackage,const FString& TargetMeshPackage)
{
    PCViewerAnimation::FInputs Inputs;FString Why;
    if(!PCViewerAnimation::Prepare(SourceAnimationPackage,TargetMeshPackage,Inputs,Why))
    {
        if(Why.Contains(TEXT("Insufficient matching animated/core bones")))
        {
            const auto Report=PCViewerAnimation::BaseReport(Inputs);
            Report->SetStringField(TEXT("status"),TEXT("not_compatible"));
            Report->SetStringField(TEXT("error"),Why);
            return PCViewerAnimation::Encode(Report);
        }
        return PCViewerAnimation::Failure(Why);
    }
    return PCViewerAnimation::Encode(PCViewerAnimation::BaseReport(Inputs));
}

FString UPCViewerAnimationLibrary::VerifyAdaptedAnimation(const FString& SourceAnimationPackage,const FString& TargetMeshPackage,const FString& TargetAnimationPackage)
{
    using namespace PCViewerAnimation;
    if(!ValidPackage(TargetAnimationPackage,TEXT("/Game/PCAssets/Animations/"))) return Failure(TEXT("Animation target must be below /Game/PCAssets/Animations/"));
    FInputs Inputs;FString Why;
    if(!Prepare(SourceAnimationPackage,TargetMeshPackage,Inputs,Why)) return Failure(Why);
    UAnimSequence* Animation=LoadObject<UAnimSequence>(nullptr,*ObjectPath(TargetAnimationPackage));
    const auto Report=BaseReport(Inputs);
    if(!Verify(Inputs,Animation,Report,Why)) return Failure(Why);
    return Encode(Report);
}

FString UPCViewerAnimationLibrary::AdaptAnimation(const FString& SourceAnimationPackage,const FString& TargetMeshPackage,
    const FString& TargetAnimationPackage,const FString& OptionsJson)
{
    using namespace PCViewerAnimation;
    if(!ValidPackage(TargetAnimationPackage,TEXT("/Game/PCAssets/Animations/"))) return Failure(TEXT("Animation target must be below /Game/PCAssets/Animations/"));
    TSharedPtr<FJsonObject> Options;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(OptionsJson),Options) || !Options.IsValid())
        return Failure(TEXT("Options must be a JSON object"));
    FString Kind;
    if(!Options->TryGetStringField(TEXT("source_kind"),Kind) ||
       (Kind!=TEXT("same_outfit_idle") && Kind!=TEXT("same_character_idle") && Kind!=TEXT("shared_idle")))
        return Failure(TEXT("source_kind must explicitly identify same_outfit_idle, same_character_idle or shared_idle"));
    FInputs Inputs;FString Why;
    if(!Prepare(SourceAnimationPackage,TargetMeshPackage,Inputs,Why)) return Failure(Why);
    if(!Inputs.HumanoidCompatible)
    {
        FString Selection;
        if(Kind!=TEXT("same_outfit_idle") || !Options->TryGetStringField(TEXT("source_selection"),Selection) ||
            Selection!=TEXT("same_outfit_existing_part_idle"))
            return Failure(TEXT("A non-humanoid accessory requires its exact existing same-outfit part idle"));
    }
    if(Kind!=TEXT("shared_idle"))
    {
        TArray<FString> Parts;SourceAnimationPackage.ParseIntoArray(Parts,TEXT("/"),true);
        const FString TargetSource=Inputs.Mesh->GetPackage()->GetMetaData().GetValue(Inputs.Mesh.Get(),TEXT("PCAssetConversion.SourcePackage"));
        const FString Family=Parts.Num()>2?Parts[2]:FString();
        if(Family.IsEmpty() || !TargetSource.Contains(TEXT("/Character/")+Family+TEXT("/"),ESearchCase::IgnoreCase))
            return Failure(TEXT("A same-character idle claim does not match the target source family"));
    }
    UObject* Existing=FindObject<UObject>(nullptr,*ObjectPath(TargetAnimationPackage));
    const bool OnDisk=FPackageName::DoesPackageExist(TargetAnimationPackage);
    if(!Existing && OnDisk) Existing=LoadObject<UObject>(nullptr,*ObjectPath(TargetAnimationPackage));
    if(Existing)
    {
        if(!Cast<UAnimSequence>(Existing) || Existing->GetPackage()->GetMetaData().GetValue(Existing,OwnerTag)!=Owner)
            return Failure(TEXT("Refusing to overwrite an unowned animation asset"));
        const auto Report=BaseReport(Inputs);
        if(!Verify(Inputs,Cast<UAnimSequence>(Existing),Report,Why)) return Failure(Why);
        Report->SetStringField(TEXT("operation"),TEXT("verified_existing"));
        return Encode(Report);
    }
    if(OnDisk) return Failure(TEXT("Target package exists without expected animation"));
    UPackage* Outer=CreatePackage(*TargetAnimationPackage);
    UAnimSequence* Animation=NewObject<UAnimSequence>(Outer,*FPackageName::GetLongPackageAssetName(TargetAnimationPackage),RF_Public|RF_Standalone|RF_Transactional);
    Animation->SetSkeleton(Inputs.Mesh->GetSkeleton());
    Animation->SetPreviewMesh(Inputs.Mesh.Get());
    Animation->bEnableRootMotion=false;
    Animation->ClearRetargetSourceAsset();
    IAnimationDataController& Controller=Animation->GetController();
    Controller.OpenBracket(FText::FromString(TEXT("Adapt existing viewer idle to PC bind basis")),false);
    Controller.InitializeModel();
    Controller.SetFrameRate(Inputs.FrameRate,false);
    Controller.SetNumberOfFrames(FFrameNumber(Inputs.Frames),false);
    TArray<TArray<FVector3f>> Positions,Scales;
    TArray<TArray<FQuat4f>> Rotations;
    Positions.SetNum(Inputs.TargetNames.Num());Scales.SetNum(Inputs.TargetNames.Num());Rotations.SetNum(Inputs.TargetNames.Num());
    TArray<FTransform> SourceGlobal,Local,Global;
    for(int32 Frame=0;Frame<Inputs.Keys;++Frame)
    {
        Evaluate(Inputs,Frame,SourceGlobal,Local,Global);
        for(int32 Bone=0;Bone<Inputs.TargetNames.Num();++Bone)
        {
            if(Local[Bone].ContainsNaN()) { Controller.CloseBracket(false);return Failure(TEXT("Retargeted key is nonfinite")); }
            Positions[Bone].Add(FVector3f(Local[Bone].GetLocation()));
            Rotations[Bone].Add(FQuat4f(Local[Bone].GetRotation()));
            Scales[Bone].Add(FVector3f(Local[Bone].GetScale3D()));
        }
    }
    for(int32 Bone=0;Bone<Inputs.TargetNames.Num();++Bone)
    {
        if(!Controller.AddBoneCurve(Inputs.TargetNames[Bone],false) ||
           !Controller.SetBoneTrackKeys(Inputs.TargetNames[Bone],Positions[Bone],Rotations[Bone],Scales[Bone],false))
        { Controller.CloseBracket(false);return Failure(TEXT("Could not write a dense target bone track")); }
    }
    int32 CorrectedEulerTracks=0;
    if(!CorrectSingularEulerKeys(Controller,Inputs.TargetNames,Rotations,CorrectedEulerTracks,Why))
    { Controller.CloseBracket(false);return Failure(Why); }
    Controller.NotifyPopulated();Controller.CloseBracket(false);
    FMetaData& Meta=Outer->GetMetaData();
    Meta.SetValue(Animation,OwnerTag,Owner);Meta.SetValue(Animation,SourceHashTag,*Inputs.SourceHash);Meta.SetValue(Animation,MeshHashTag,*Inputs.MeshHash);
    Meta.SetValue(Animation,TEXT("PCViewerAnimation.SourceKind"),*Kind);
    Meta.SetValue(Animation,TEXT("PCViewerAnimation.OptionsJSON"),*OptionsJson);
    Meta.SetValue(Animation,TEXT("PCViewerAnimation.SourceAnimation"),*SourceAnimationPackage);
    Meta.SetValue(Animation,TEXT("PCViewerAnimation.TargetMesh"),*TargetMeshPackage);
    Meta.SetValue(Animation,TEXT("PCViewerAnimation.CorrectedEulerTracks"),*FString::FromInt(CorrectedEulerTracks));
    Animation->PostEditChange();Animation->MarkPackageDirty();
    const auto Report=BaseReport(Inputs);
    if(!Verify(Inputs,Animation,Report,Why)) return Failure(Why);
    FAssetRegistryModule::AssetCreated(Animation);
    if(!Save(Animation)) return Failure(TEXT("Could not save verified adapted animation"));
    Report->SetStringField(TEXT("operation"),TEXT("adapted_existing_idle_and_saved"));
    return Encode(Report);
}
