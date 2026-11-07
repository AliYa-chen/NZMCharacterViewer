#include "ViewerAnimationImportLibrary.h"
#include "Animation/AnimSequence.h"
#include "Engine/SkeletalMesh.h"

#if WITH_EDITOR
#include "Animation/AnimData/IAnimationDataController.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "ReferenceSkeleton.h"

namespace
{
    FVector ReadVector(const TArray<TSharedPtr<FJsonValue>>& Values)
    {
        return FVector(Values[0]->AsNumber(), Values[1]->AsNumber(), Values[2]->AsNumber());
    }

    FQuat ReadQuaternion(const TArray<TSharedPtr<FJsonValue>>& Values)
    {
        return FQuat(Values[0]->AsNumber(), Values[1]->AsNumber(), Values[2]->AsNumber(), Values[3]->AsNumber()).GetNormalized();
    }

    FTransform ReadReference(const TSharedPtr<FJsonObject>& Object)
    {
        const auto Rotation = Object->GetObjectField(TEXT("Rotation"));
        const auto Position = Object->GetObjectField(TEXT("Translation"));
        const auto Scale = Object->GetObjectField(TEXT("Scale3D"));
        return FTransform(FQuat(Rotation->GetNumberField(TEXT("X")), Rotation->GetNumberField(TEXT("Y")),
            Rotation->GetNumberField(TEXT("Z")), Rotation->GetNumberField(TEXT("W"))).GetNormalized(),
            FVector(Position->GetNumberField(TEXT("X")), Position->GetNumberField(TEXT("Y")), Position->GetNumberField(TEXT("Z"))),
            FVector(Scale->GetNumberField(TEXT("X")), Scale->GetNumberField(TEXT("Y")), Scale->GetNumberField(TEXT("Z"))));
    }

    struct FBoneCurves
    {
        FName Name;
        TArray<FVector3f> Positions;
        TArray<FQuat4f> Rotations;
        TArray<FVector3f> Scales;
    };
}
#endif

bool UViewerAnimationImportLibrary::PopulateAnimationFromCurves(UAnimSequence* Animation,
    USkeletalMesh* TargetMesh, const FString& JsonFile, FString& Report)
{
#if WITH_EDITOR
    auto Fail = [&Report](const FString& Error) { Report = Error; return false; };
    if (!Animation || !TargetMesh || Animation->GetSkeleton() != TargetMesh->GetSkeleton())
        return Fail(TEXT("Animation must use the target mesh's existing skeleton."));
    FString Text;
    TSharedPtr<FJsonObject> Root;
    if (!FFileHelper::LoadFileToString(Text, *JsonFile)
        || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root)
        return Fail(TEXT("Cannot read curve JSON."));
    const TArray<TSharedPtr<FJsonValue>>* Sequences = nullptr;
    if (!Root->TryGetArrayField(TEXT("sequences"), Sequences) || Sequences->Num() != 1)
        return Fail(TEXT("Expected exactly one decoded animation sequence."));
    const auto Sequence = (*Sequences)[0]->AsObject();
    const auto& SourceBones = Sequence->GetArrayField(TEXT("bones"));
    const auto& Tracks = Sequence->GetArrayField(TEXT("tracks"));
    const int32 Frames = Sequence->GetIntegerField(TEXT("frames"));
    const double Duration = Sequence->GetNumberField(TEXT("sequence_length"));
    if (Frames < 2 || Frames > 60000 || !FMath::IsFinite(Duration) || Duration <= 0.0 || SourceBones.IsEmpty())
        return Fail(TEXT("Invalid sample count, duration or reference skeleton."));

    TMap<FName, int32> SourceIndices;
    TArray<int32> SourceParents;
    TArray<FTransform> SourceReference, SourceGlobalReference;
    for (int32 Index = 0; Index < SourceBones.Num(); ++Index)
    {
        const auto Bone = SourceBones[Index]->AsObject();
        const FName Name(*Bone->GetStringField(TEXT("name")));
        const int32 Parent = Bone->GetIntegerField(TEXT("parent"));
        const FTransform Reference = ReadReference(Bone->GetObjectField(TEXT("reference_pose")));
        if (SourceIndices.Contains(Name) || Parent < INDEX_NONE || Parent >= Index || Reference.ContainsNaN())
            return Fail(TEXT("Invalid source bone hierarchy or reference transform."));
        SourceIndices.Add(Name, Index);
        SourceParents.Add(Parent);
        SourceReference.Add(Reference);
        SourceGlobalReference.Add(Parent == INDEX_NONE ? Reference : Reference * SourceGlobalReference[Parent]);
    }
    TArray<TArray<FTransform>> SourceKeys;
    SourceKeys.SetNum(SourceBones.Num());
    for (const auto& TrackValue : Tracks)
    {
        const auto Track = TrackValue->AsObject();
        const int32* Index = SourceIndices.Find(FName(*Track->GetStringField(TEXT("bone"))));
        if (!Index || !SourceKeys[*Index].IsEmpty()) return Fail(TEXT("Unknown or duplicate bone track."));
        const auto& Positions = Track->GetArrayField(TEXT("positions"));
        const auto& Rotations = Track->GetArrayField(TEXT("rotations"));
        const auto& Scales = Track->GetArrayField(TEXT("scales"));
        if (Positions.Num() != Frames || Rotations.Num() != Frames || Scales.Num() != Frames)
            return Fail(TEXT("All channels must contain dense samples, including the end frame."));
        SourceKeys[*Index].Reserve(Frames);
        for (int32 Frame = 0; Frame < Frames; ++Frame)
        {
            const auto& P = Positions[Frame]->AsArray();
            const auto& Q = Rotations[Frame]->AsArray();
            const auto& S = Scales[Frame]->AsArray();
            if (P.Num() != 3 || Q.Num() != 4 || S.Num() != 3)
                return Fail(TEXT("Invalid bone transform channel dimensions."));
            FTransform Transform(ReadQuaternion(Q), ReadVector(P), ReadVector(S));
            if (Transform.ContainsNaN()) return Fail(TEXT("Non-finite animation transform."));
            SourceKeys[*Index].Add(Transform);
        }
    }

    const FReferenceSkeleton& Target = TargetMesh->GetRefSkeleton();
    const auto& TargetReference = Target.GetRefBonePose();
    TArray<FTransform> TargetGlobalReference, BasisCorrections;
    TArray<int32> TargetToSource;
    TArray<FBoneCurves> Result;
    int32 Matched = 0;
    double MaxReferencePositionDifference = 0.0;
    for (int32 Index = 0; Index < Target.GetNum(); ++Index)
    {
        const FName Name = Target.GetBoneName(Index);
        const int32 Parent = Target.GetParentIndex(Index);
        TargetGlobalReference.Add(Parent == INDEX_NONE ? TargetReference[Index] : TargetReference[Index] * TargetGlobalReference[Parent]);
        const int32* SourceIndex = SourceIndices.Find(Name);
        TargetToSource.Add(SourceIndex ? *SourceIndex : INDEX_NONE);
        BasisCorrections.Add(SourceIndex ? TargetGlobalReference[Index] * SourceGlobalReference[*SourceIndex].Inverse() : FTransform::Identity);
        if (SourceIndex)
        {
            ++Matched;
            MaxReferencePositionDifference = FMath::Max(MaxReferencePositionDifference,
                FVector::Distance(TargetGlobalReference[Index].GetLocation(), SourceGlobalReference[*SourceIndex].GetLocation()));
        }
        FBoneCurves& Curves = Result.AddDefaulted_GetRef();
        Curves.Name = Name;
        Curves.Positions.Reserve(Frames); Curves.Rotations.Reserve(Frames); Curves.Scales.Reserve(Frames);
    }
    if (Matched < 3) return Fail(TEXT("Source and target skeletons have insufficient matching bones."));
    TArray<FTransform> SourceGlobalPose, TargetGlobalPose;
    SourceGlobalPose.SetNum(SourceBones.Num()); TargetGlobalPose.SetNum(Target.GetNum());
    for (int32 Frame = 0; Frame < Frames; ++Frame)
    {
        for (int32 Index = 0; Index < SourceBones.Num(); ++Index)
        {
            const FTransform& Local = SourceKeys[Index].IsEmpty() ? SourceReference[Index] : SourceKeys[Index][Frame];
            SourceGlobalPose[Index] = SourceParents[Index] == INDEX_NONE ? Local : Local * SourceGlobalPose[SourceParents[Index]];
        }
        for (int32 Index = 0; Index < Target.GetNum(); ++Index)
        {
            const int32 Parent = Target.GetParentIndex(Index), SourceIndex = TargetToSource[Index];
            // Blender may orient bone axes differently while preserving the same
            // bind geometry. Express source skinning motion in each existing
            // target bone's reference basis, then recover target-local curves.
            const FTransform Global = SourceIndex != INDEX_NONE ? BasisCorrections[Index] * SourceGlobalPose[SourceIndex]
                : Parent == INDEX_NONE ? TargetReference[Index] : TargetReference[Index] * TargetGlobalPose[Parent];
            TargetGlobalPose[Index] = Global;
            FTransform Local = Parent == INDEX_NONE ? Global : Global.GetRelativeTransform(TargetGlobalPose[Parent]);
            Local.NormalizeRotation();
            if (Local.ContainsNaN()) return Fail(TEXT("Retargeting produced a non-finite transform."));
            Result[Index].Positions.Add(FVector3f(Local.GetLocation()));
            Result[Index].Rotations.Add(FQuat4f(Local.GetRotation()));
            Result[Index].Scales.Add(FVector3f(Local.GetScale3D()));
        }
    }
    IAnimationDataController& Controller = Animation->GetController();
    Controller.OpenBracket(FText::FromString(TEXT("Import decoded viewer animation")), false);
    Controller.InitializeModel();
    const int32 Denominator = 100000;
    Controller.SetFrameRate(FFrameRate(FMath::RoundToInt((Frames - 1) / Duration * Denominator), Denominator), false);
    Controller.SetNumberOfFrames(FFrameNumber(Frames - 1), false);
    for (const FBoneCurves& Curves : Result)
    {
        Controller.AddBoneCurve(Curves.Name, false);
        Controller.SetBoneTrackKeys(Curves.Name, Curves.Positions, Curves.Rotations, Curves.Scales, false);
    }
    Controller.NotifyPopulated();
    Controller.CloseBracket(false);
    Animation->PostEditChange();
    Animation->MarkPackageDirty();
    Report = FString::Printf(TEXT("{\"matched_bones\":%d,\"target_bones\":%d,\"source_bones\":%d,\"frames\":%d,\"duration\":%.9f,\"max_reference_position_difference_cm\":%.9f}"),
        Matched, Target.GetNum(), SourceBones.Num(), Frames, Duration, MaxReferencePositionDifference);
    return true;
#else
    Report = TEXT("Animation curve import is available only in the editor.");
    return false;
#endif
}
