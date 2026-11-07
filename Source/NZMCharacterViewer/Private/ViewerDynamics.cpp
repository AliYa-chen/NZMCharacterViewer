#include "ViewerDynamics.h"

#include "Animation/AnimSingleNodeInstance.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "CoreGlobals.h"
#include "ReferenceSkeleton.h"
#include "Templates/UnrealTemplate.h"

namespace ViewerDynamics
{
    static FVector LimitDirection(const FVector& Direction, const FVector& AnimatedDirection, float MaxDegrees)
    {
        const FVector A = AnimatedDirection.GetSafeNormal();
        const FVector B = Direction.GetSafeNormal();
        if (A.IsNearlyZero() || B.IsNearlyZero()) return A;
        const double Angle = FMath::Acos(FMath::Clamp(FVector::DotProduct(A, B), -1.0, 1.0));
        const double Limit = FMath::DegreesToRadians(static_cast<double>(MaxDegrees));
        if (Angle <= Limit || Angle < UE_SMALL_NUMBER) return B;
        return FQuat::Slerp(FQuat::Identity, FQuat::FindBetweenNormals(A, B), Limit / Angle).RotateVector(A).GetSafeNormal();
    }

    static float FiniteUnit(float Value, float Fallback)
    {
        return FMath::IsFinite(Value) ? FMath::Clamp(Value, 0.f, 1.f) : Fallback;
    }
}

UViewerSkeletalMeshComponent::UViewerSkeletalMeshComponent()
{
    // A fresh animation/ref pose must be produced even for a stationary character.
    // We never feed a previously modified render pose back into animation caches.
    VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
    bEnableUpdateRateOptimizations = false;
}

void UViewerSkeletalMeshComponent::SetDynamicsProfile(UViewerDynamicsProfile* InProfile)
{
    check(IsInGameThread());
    if (DynamicsProfile == InProfile) return;
    DynamicsProfile = InProfile;
    ResetSecondaryMotion();
}

void UViewerSkeletalMeshComponent::SetSecondaryMotionEnabled(bool bInEnabled)
{
    check(IsInGameThread());
    if (bSecondaryMotionEnabled == bInEnabled) return;
    bSecondaryMotionEnabled = bInEnabled;
    ResetSecondaryMotion();
    // Disable immediately reevaluates the unmodified animation pose. This uses
    // the normal engine path, including completion of a pending parallel eval.
    if (!bInEnabled && !bInsideFinalize && IsRegistered() && GetSkeletalMeshAsset())
    {
        RefreshBoneTransforms();
        MarkRenderDynamicDataDirty();
    }
}

void UViewerSkeletalMeshComponent::ResetSecondaryMotion()
{
    check(IsInGameThread());
    ChainStates.Reset();
    CachedMesh.Reset();
    bStateInitialized = false;
    LastSolvedFrame = MAX_uint64;
    LastMaxDisplacementCm = 0.f;
    PreviousComponentTransform = GetComponentTransform();
}

void UViewerSkeletalMeshComponent::OnRegister()
{
    AnimatedBoundsMesh.Reset();
    AnimatedLocalBounds = FBox(ForceInit);
    Super::OnRegister();
    ResetSecondaryMotion();
}

void UViewerSkeletalMeshComponent::OnUnregister()
{
    AnimatedBoundsMesh.Reset();
    AnimatedLocalBounds = FBox(ForceInit);
    ResetSecondaryMotion();
    Super::OnUnregister();
}

void UViewerSkeletalMeshComponent::TickComponent(float DeltaTime, ELevelTick TickType,
                                                FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    if (!IsInGameThread() || !bSecondaryMotionEnabled || !DynamicsProfile ||
        !GetSkeletalMeshAsset() || IsSimulatingPhysics() ||
        GetAnimationMode() != EAnimationMode::AnimationSingleNode ||
        LastSolvedFrame == GFrameCounter)
    {
        return;
    }
    UAnimSingleNodeInstance* SingleNode = GetSingleNodeInstance();
    if (SingleNode && SingleNode->GetCurrentAsset()) return;

    // An installed-engine header cannot establish whether an empty SingleNode
    // publishes a new reference pose every frame. Explicitly generate one only
    // when the normal path has not already solved this frame. The public getter
    // waits for any outstanding parallel evaluation, which can itself Finalize.
    (void)GetBoneSpaceTransforms();
    if (LastSolvedFrame == GFrameCounter || bInsideFinalize || !IsRegistered()) return;
    const USkeletalMesh* Mesh = GetSkeletalMeshAsset();
    if (!Mesh) return;
    const FReferenceSkeleton& Ref = Mesh->GetRefSkeleton();
    const TArray<FTransform>& ReferencePose = Ref.GetRefBonePose();
    if (ReferencePose.Num() != Ref.GetNum()) return;
    TArray<FTransform>& PendingPose = GetEditableComponentSpaceTransforms();
    PendingPose.SetNumUninitialized(ReferencePose.Num());
    for (int32 Bone = 0; Bone < ReferencePose.Num(); ++Bone)
    {
        const int32 Parent = Ref.GetParentIndex(Bone);
        PendingPose[Bone] = Parent >= 0 ? ReferencePose[Bone] * PendingPose[Parent] : ReferencePose[Bone];
        PendingPose[Bone].NormalizeRotation();
    }
    bNeedToFlipSpaceBaseBuffers = true;
    FinalizeBoneTransform();
    MarkRenderDynamicDataDirty();
}

void UViewerSkeletalMeshComponent::FinalizeBoneTransform()
{
    // UE 5.8 exposes this mutable *pending* buffer publicly; the pending-flip
    // flag is protected. Editing before Super publishes it avoids a frame of
    // latency and preserves the engine's revision, follower and delegate path.
    TGuardValue<bool> Guard(bInsideFinalize, true);
    if (IsInGameThread() && bNeedToFlipSpaceBaseBuffers && bSecondaryMotionEnabled &&
        DynamicsProfile && GetSkeletalMeshAsset() && !IsSimulatingPhysics() &&
        LastSolvedFrame != GFrameCounter)
    {
        LastSolvedFrame = GFrameCounter;
        TArray<FTransform>& PendingPose = GetEditableComponentSpaceTransforms();
        if (PendingPose.Num() == GetSkeletalMeshAsset()->GetRefSkeleton().GetNum())
        {
            const float Delta = GetWorld() ? GetWorld()->GetDeltaSeconds() : 0.f;
            if (FMath::IsFinite(Delta) && Delta > 0.f)
            {
                ApplySecondaryMotion(PendingPose, Delta);
            }
        }
    }
    Super::FinalizeBoneTransform();
    // A clip can place its skeleton well outside the imported reference-pose
    // box (the seated Queen is about a metre away). Publish fresh bounds even
    // for hidden preparation poses, before they become visible to the renderer.
    UpdateAnimatedBounds();
}

void UViewerSkeletalMeshComponent::UpdateAnimatedBounds()
{
    if (!IsInGameThread()) return;
    USkeletalMesh* Mesh = GetSkeletalMeshAsset();
    if (!Mesh) return;
    // Read only the already-published CPU pose. Waiting for animation here or
    // reading GPU-skinned vertices could recurse into finalization or stall it.
    const TArray<FTransform>& Pose = GetComponentSpaceTransforms();
    FBox PoseBox(ForceInit);
    for (const FTransform& Bone : Pose)
    {
        const FVector Position = Bone.GetLocation();
        if (!Position.ContainsNaN()) PoseBox += Position;
    }
    if (!PoseBox.IsValid) return;
    // Allow flesh, clothing and hair around the bone centres. Base imported
    // bounds remain in the final union, so this extends rather than shrinks the
    // mesh's known reference geometry without an arbitrary bounds multiplier.
    const FVector Extent = Mesh->GetImportedBounds().BoxExtent;
    const double Margin = Extent.ContainsNaN() ? 5.0 : FMath::Clamp(Extent.GetAbsMin(), 5.0, 25.0);
    PoseBox = PoseBox.ExpandBy(Margin);
    const bool Changed = AnimatedBoundsMesh.Get() != Mesh || !AnimatedLocalBounds.IsValid
        || !AnimatedLocalBounds.Min.Equals(PoseBox.Min, 0.1)
        || !AnimatedLocalBounds.Max.Equals(PoseBox.Max, 0.1);
    AnimatedBoundsMesh = Mesh;
    AnimatedLocalBounds = PoseBox;
    if (Changed)
    {
        InvalidateCachedBounds();
        UpdateBounds();
        MarkRenderTransformDirty();
    }
}

FBoxSphereBounds UViewerSkeletalMeshComponent::CalcBounds(const FTransform& LocalToWorld) const
{
    const FBoxSphereBounds Reference = Super::CalcBounds(LocalToWorld);
    if (AnimatedBoundsMesh.Get() != GetSkeletalMeshAsset() || !AnimatedLocalBounds.IsValid
        || LocalToWorld.ContainsNaN()) return Reference;
    const FBox PoseBox = AnimatedLocalBounds.TransformBy(LocalToWorld);
    if (!PoseBox.IsValid || PoseBox.Min.ContainsNaN() || PoseBox.Max.ContainsNaN()) return Reference;
    FBox Combined = Reference.GetBox();
    Combined += PoseBox;
    return FBoxSphereBounds(Combined);
}

void UViewerSkeletalMeshComponent::SeedChain(FChainState& State, const TArray<FVector>& Goals)
{
    State.Positions.SetNum(State.Bones.Num());
    State.PreviousPositions.SetNum(State.Bones.Num());
    for (int32 I = 0; I < State.Bones.Num(); ++I)
    {
        State.Positions[I] = Goals[State.Bones[I]];
        State.PreviousPositions[I] = State.Positions[I];
    }
    State.Accumulator = 0.f;
}

void UViewerSkeletalMeshComponent::RebuildChains(const TArray<FTransform>& AnimationPose)
{
    ChainStates.Reset();
    const USkeletalMesh* Mesh = GetSkeletalMeshAsset();
    if (!Mesh || !DynamicsProfile) return;
    const FReferenceSkeleton& Ref = Mesh->GetRefSkeleton();
    TSet<int32> ClaimedBones;
    for (int32 SettingsIndex = 0; SettingsIndex < DynamicsProfile->Chains.Num(); ++SettingsIndex)
    {
        const FViewerDynamicChain& Settings = DynamicsProfile->Chains[SettingsIndex];
        const int32 RootIndex = Ref.FindBoneIndex(Settings.RootBone);
        if (RootIndex == INDEX_NONE || ClaimedBones.Contains(RootIndex)) continue;

        FChainState State;
        State.SettingsIndex = SettingsIndex;
        State.Bones.Add(RootIndex);
        State.ParentParticles.Add(INDEX_NONE);
        TMap<int32, int32> ParticleForBone;
        ParticleForBone.Add(RootIndex, 0);
        const int32 MaxBones = FMath::Clamp(Settings.MaxBones, 2, 128);
        // Reference skeletons are parent-before-child. Missing/excluded/claimed
        // parents stop traversal, so disjoint chains never overwrite each other.
        for (int32 Bone = RootIndex + 1; Bone < Ref.GetNum() && State.Bones.Num() < MaxBones; ++Bone)
        {
            const int32* ParentParticle = ParticleForBone.Find(Ref.GetParentIndex(Bone));
            if (!ParentParticle || ClaimedBones.Contains(Bone) || Settings.ExcludeBones.Contains(Ref.GetBoneName(Bone))) continue;
            const int32 NewParticle = State.Bones.Add(Bone);
            State.ParentParticles.Add(*ParentParticle);
            ParticleForBone.Add(Bone, NewParticle);
        }
        if (State.Bones.Num() < 2) continue;
        for (const int32 Bone : State.Bones) ClaimedBones.Add(Bone);
        ChainStates.Add(MoveTemp(State));
    }

    TArray<FVector> Goals;
    Goals.Reserve(AnimationPose.Num());
    for (const FTransform& Transform : AnimationPose)
    {
        Goals.Add(GetComponentTransform().TransformPosition(Transform.GetLocation()));
    }
    for (FChainState& State : ChainStates) SeedChain(State, Goals);
    CachedMesh = GetSkeletalMeshAsset();
    PreviousComponentTransform = GetComponentTransform();
    bStateInitialized = true;
}

bool UViewerSkeletalMeshComponent::SolveChain(FChainState& State, const FViewerDynamicChain& Settings,
                                             const TArray<FVector>& Goals, const FTransform& ComponentTransform,
                                             float DeltaSeconds)
{
    const int32 Count = State.Bones.Num();
    if (Count < 2 || State.Positions.Num() != Count) return false;
    const float Rate = static_cast<float>(FMath::Clamp(Settings.TargetFramerate, 15, 120));
    const float Step = 1.f / Rate;
    const float Damping = ViewerDynamics::FiniteUnit(Settings.Damping, 0.3f);
    const float Stiffness = ViewerDynamics::FiniteUnit(Settings.Stiffness, 0.25f);
    const float CarryLocation = Settings.bIgnoreMoveInfluence ? 1.f : 1.f - ViewerDynamics::FiniteUnit(Settings.WorldLocationDamping, 0.8f);
    const float CarryRotation = Settings.bIgnoreMoveInfluence ? 1.f : 1.f - ViewerDynamics::FiniteUnit(Settings.WorldRotationDamping, 0.8f);
    const FQuat DeltaRotation = ComponentTransform.GetRotation() * PreviousComponentTransform.GetRotation().Inverse();
    const FQuat Carry = FQuat::Slerp(FQuat::Identity, DeltaRotation, CarryRotation).GetNormalized();
    const FVector SafeMoveMultiplier(
        FMath::Clamp(Settings.MoveMultiplier.X, 0., 3.),
        FMath::Clamp(Settings.MoveMultiplier.Y, 0., 3.),
        FMath::Clamp(Settings.MoveMultiplier.Z, 0., 3.));
    const FVector Translation = (ComponentTransform.GetLocation() - PreviousComponentTransform.GetLocation()) * CarryLocation * SafeMoveMultiplier;
    const FVector Origin = PreviousComponentTransform.GetLocation();
    for (int32 I = 0; I < Count; ++I)
    {
        State.Positions[I] = Origin + Carry.RotateVector(State.Positions[I] - Origin) + Translation;
        State.PreviousPositions[I] = Origin + Carry.RotateVector(State.PreviousPositions[I] - Origin) + Translation;
    }

    const float SafeMaxDelta = FMath::Clamp(DynamicsProfile->MaxDeltaSeconds, 1.f / 120.f, 0.1f);
    State.Accumulator = FMath::Min(State.Accumulator + FMath::Min(DeltaSeconds, SafeMaxDelta), SafeMaxDelta);
    const int32 Substeps = FMath::Min(FMath::FloorToInt(State.Accumulator / Step), FMath::Clamp(DynamicsProfile->MaxSubsteps, 1, 8));
    State.Accumulator -= Substeps * Step;
    if (State.Accumulator >= Step) State.Accumulator = 0.f; // drop excessive hitch debt

    const FVector Force = (Settings.Gravity + Settings.ConstantForce).GetClampedToMaxSize(9800.f);
    const FVector VelocityScale = Settings.VelocityMultiplier.GetClampedToSize(0., 3.);
    const double MaxDisplacement = FMath::Clamp(static_cast<double>(Settings.MaxDisplacementCm), 0.1, 50.0);
    float MaxAngle = FMath::Clamp(Settings.MaxBendDegrees, 1.f, 80.f);
    if (Settings.LimitAngle > 0.f) MaxAngle = FMath::Min(MaxAngle, Settings.LimitAngle);
    TArray<double, TInlineAllocator<128>> Lengths;
    Lengths.SetNumZeroed(Count);
    for (int32 I = 1; I < Count; ++I)
    {
        Lengths[I] = FVector::Distance(Goals[State.Bones[I]], Goals[State.Bones[State.ParentParticles[I]]]);
    }

    auto Constrain = [&]()
    {
        State.Positions[0] = Goals[State.Bones[0]]; // pinned root position
        State.PreviousPositions[0] = State.Positions[0];
        for (int32 Iteration = 0; Iteration < FMath::Clamp(DynamicsProfile->ConstraintIterations, 1, 8); ++Iteration)
        {
            for (int32 I = 1; I < Count; ++I)
            {
                const int32 Parent = State.ParentParticles[I];
                const FVector AnimatedDirection = Goals[State.Bones[I]] - Goals[State.Bones[Parent]];
                const FVector Direction = ViewerDynamics::LimitDirection(State.Positions[I] - State.Positions[Parent], AnimatedDirection, MaxAngle);
                State.Positions[I] = State.Positions[Parent] + Direction * Lengths[I];
                const FVector Deviation = State.Positions[I] - Goals[State.Bones[I]];
                if (Deviation.SizeSquared() > FMath::Square(MaxDisplacement))
                {
                    State.Positions[I] = Goals[State.Bones[I]] + Deviation.GetClampedToMaxSize(MaxDisplacement);
                }
            }
        }
    };

    for (int32 Substep = 0; Substep < Substeps; ++Substep)
    {
        for (int32 I = 1; I < Count; ++I)
        {
            const FVector Current = State.Positions[I];
            FVector Velocity = (Current - State.PreviousPositions[I]) * (1.f - Damping) * VelocityScale;
            Velocity = Velocity.GetClampedToMaxSize(MaxDisplacement);
            const FVector Acceleration = (Goals[State.Bones[I]] - Current) * (Stiffness * Rate * Rate) + Force;
            State.Positions[I] = Current + Velocity + Acceleration * Step * Step;
            State.PreviousPositions[I] = Current;
        }
        Constrain();
    }
    if (Substeps == 0) Constrain();

    // Finish with exact animated segment lengths. If incompatible safety bounds
    // or malformed input cannot satisfy them, reset the entire chain to the
    // unmodified pose instead of emitting a stretched or non-finite skeleton.
    for (int32 I = 1; I < Count; ++I)
    {
        const int32 Parent = State.ParentParticles[I];
        const FVector Direction = ViewerDynamics::LimitDirection(State.Positions[I] - State.Positions[Parent],
            Goals[State.Bones[I]] - Goals[State.Bones[Parent]], MaxAngle);
        State.Positions[I] = State.Positions[Parent] + Direction * Lengths[I];
        if (State.Positions[I].ContainsNaN() || FVector::DistSquared(State.Positions[I], Goals[State.Bones[I]]) > FMath::Square(MaxDisplacement * 1.01))
        {
            SeedChain(State, Goals);
            return false;
        }
    }
    return true;
}

void UViewerSkeletalMeshComponent::ApplySecondaryMotion(TArray<FTransform>& Pose, float DeltaSeconds)
{
    LastMaxDisplacementCm = 0.f;
    if (!DynamicsProfile || !GetSkeletalMeshAsset()) return;
    const FTransform ComponentTransform = GetComponentTransform();
    if (ComponentTransform.ContainsNaN()) return;
    const FVector Scale = ComponentTransform.GetScale3D();
    if (FMath::Abs(Scale.X) < UE_SMALL_NUMBER || FMath::Abs(Scale.Y) < UE_SMALL_NUMBER || FMath::Abs(Scale.Z) < UE_SMALL_NUMBER) return;
    for (const FTransform& Transform : Pose) if (Transform.ContainsNaN()) return;
    const bool bChangedMesh = CachedMesh.Get() != GetSkeletalMeshAsset();
    const bool bScaleChanged = !PreviousComponentTransform.GetScale3D().Equals(ComponentTransform.GetScale3D(), 0.001);
    const bool bTeleport = bStateInitialized &&
        (FVector::Distance(PreviousComponentTransform.GetLocation(), ComponentTransform.GetLocation()) > FMath::Max(1.f, DynamicsProfile->TeleportDistanceCm) ||
         FMath::RadiansToDegrees(PreviousComponentTransform.GetRotation().AngularDistance(ComponentTransform.GetRotation())) > FMath::Clamp(DynamicsProfile->TeleportRotationDegrees, 1.f, 180.f));
    if (!bStateInitialized || bChangedMesh || bScaleChanged || bTeleport || DeltaSeconds > 0.25f)
    {
        RebuildChains(Pose);
        return; // resets never perturb the first pose
    }
    if (ChainStates.IsEmpty()) return;

    const TArray<FTransform> AnimationPose = Pose;
    const FReferenceSkeleton& Ref = GetSkeletalMeshAsset()->GetRefSkeleton();
    TArray<FVector> Goals;
    Goals.Reserve(Pose.Num());
    for (const FTransform& Transform : AnimationPose) Goals.Add(ComponentTransform.TransformPosition(Transform.GetLocation()));
    TMap<int32, FVector> SolvedPositions;
    TMap<int32, FQuat> SolvedWorldRotations;
    for (FChainState& State : ChainStates)
    {
        if (!DynamicsProfile->Chains.IsValidIndex(State.SettingsIndex)) continue;
        SolveChain(State, DynamicsProfile->Chains[State.SettingsIndex], Goals, ComponentTransform, DeltaSeconds);
        for (int32 I = 0; I < State.Bones.Num(); ++I)
        {
            const int32 Bone = State.Bones[I];
            SolvedPositions.Add(Bone, State.Positions[I]);
            // The first child defines this joint's primary aim. Other branches
            // keep their original local rotation and follow the edited parent.
            const int32 Child = State.ParentParticles.Find(I);
            if (Child != INDEX_NONE)
            {
                const FVector A = (Goals[State.Bones[Child]] - Goals[Bone]).GetSafeNormal();
                const FVector B = (State.Positions[Child] - State.Positions[I]).GetSafeNormal();
                if (!A.IsNearlyZero() && !B.IsNearlyZero())
                {
                    const FQuat Rotation = FQuat::FindBetweenNormals(A, B) * ComponentTransform.TransformRotation(AnimationPose[Bone].GetRotation());
                    SolvedWorldRotations.Add(Bone, Rotation.GetNormalized());
                }
            }
        }
    }

    for (const TPair<int32, FVector>& Particle : SolvedPositions)
    {
        LastMaxDisplacementCm = FMath::Max(LastMaxDisplacementCm,
            static_cast<float>(FVector::Distance(Particle.Value, Goals[Particle.Key])));
    }

    // Rebuild parent-before-child so non-simulated descendants retain their
    // animation-local transforms instead of being left behind in component space.
    for (int32 Bone = 0; Bone < Pose.Num(); ++Bone)
    {
        const int32 Parent = Ref.GetParentIndex(Bone);
        if (Parent >= 0)
        {
            const FTransform Local = AnimationPose[Bone].GetRelativeTransform(AnimationPose[Parent]);
            Pose[Bone] = Local * Pose[Parent];
        }
        if (const FVector* Position = SolvedPositions.Find(Bone)) Pose[Bone].SetLocation(ComponentTransform.InverseTransformPosition(*Position));
        if (const FQuat* Rotation = SolvedWorldRotations.Find(Bone)) Pose[Bone].SetRotation(ComponentTransform.InverseTransformRotation(*Rotation).GetNormalized());
        if (Pose[Bone].ContainsNaN())
        {
            Pose = AnimationPose;
            ResetSecondaryMotion();
            return;
        }
    }
    PreviousComponentTransform = ComponentTransform;
    InvalidateCachedBounds();
}
