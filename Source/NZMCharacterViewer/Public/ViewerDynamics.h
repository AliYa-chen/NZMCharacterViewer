#pragma once

#include "CoreMinimal.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/DataAsset.h"
#include "ViewerDynamics.generated.h"

/** Original parameters with explicit viewer safety limits. The original solver is unknown. */
USTRUCT(BlueprintType)
struct NZMCHARACTERVIEWER_API FViewerDynamicChain
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Chain")
    FName RootBone;

    /** Excluded bones also stop traversal into their children. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Chain")
    TArray<FName> ExcludeBones;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0", ClampMax="1"))
    float Stiffness = 0.25f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0", ClampMax="1"))
    float Damping = 0.3f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0", ClampMax="1"))
    float WorldLocationDamping = 0.8f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0", ClampMax="1"))
    float WorldRotationDamping = 0.8f;

    /** Recorded collision radius, in source centimetres. No original collision solver is claimed. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0"))
    float Radius = 3.f;

    /** Source zero means unrestricted; the viewer's safety cone still applies. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="0", ClampMax="180"))
    float LimitAngle = 0.f;

    /** Retained for provenance. This reconstruction always preserves segment length. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="1"))
    float MaxStretchRatio = 1.f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters", meta=(ClampMin="15", ClampMax="120"))
    int32 TargetFramerate = 40;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters")
    bool bIgnoreMoveInfluence = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters")
    FVector ConstantForce = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters")
    FVector Gravity = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters")
    FVector VelocityMultiplier = FVector::OneVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Original parameters")
    FVector MoveMultiplier = FVector::OneVector;

    /** Added safety bound, not an original game parameter. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="0.1", ClampMax="50", Units="cm"))
    float MaxDisplacementCm = 8.f;

    /** Added safety bound, not an original game parameter. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="1", ClampMax="80", Units="deg"))
    float MaxBendDegrees = 35.f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="2", ClampMax="128"))
    int32 MaxBones = 64;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Source")
    FString SourceNode;
};

UCLASS(BlueprintType)
class NZMCHARACTERVIEWER_API UViewerDynamicsProfile : public UDataAsset
{
    GENERATED_BODY()

public:
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Secondary motion")
    TArray<FViewerDynamicChain> Chains;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="0.008", ClampMax="0.1"))
    float MaxDeltaSeconds = 1.f / 15.f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="1", ClampMax="8"))
    int32 MaxSubsteps = 4;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="1", ClampMax="8"))
    int32 ConstraintIterations = 4;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="1", Units="cm"))
    float TeleportDistanceCm = 100.f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Viewer safety", meta=(ClampMin="1", ClampMax="180", Units="deg"))
    float TeleportRotationDegrees = 60.f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Source")
    FString SourceAssetPath;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Source", meta=(MultiLine="true"))
    FString ReconstructionNotes = TEXT("Parameter-guided secondary-motion reconstruction. Original DynamicBone solver, collisions and cloth simulation are not reproduced.");
};

/**
 * Applies bounded chain inertia to a newly evaluated animation pose before the
 * engine publishes its editable component-space buffer. No private pose arrays
 * or const-casts are used. This component is not a replacement for the source
 * DynamicBone plugin and does not claim identical dynamics.
 */
UCLASS(ClassGroup=(Viewer), meta=(BlueprintSpawnableComponent))
class NZMCHARACTERVIEWER_API UViewerSkeletalMeshComponent : public USkeletalMeshComponent
{
    GENERATED_BODY()

public:
    UViewerSkeletalMeshComponent();

    UFUNCTION(BlueprintCallable, Category="Viewer|Secondary motion")
    void SetDynamicsProfile(UViewerDynamicsProfile* InProfile);

    UFUNCTION(BlueprintCallable, Category="Viewer|Secondary motion")
    void SetSecondaryMotionEnabled(bool bInEnabled);

    UFUNCTION(BlueprintCallable, Category="Viewer|Secondary motion")
    void ResetSecondaryMotion();

    UFUNCTION(BlueprintPure, Category="Viewer|Secondary motion")
    bool IsSecondaryMotionEnabled() const { return bSecondaryMotionEnabled; }

    /** Read-only development diagnostics; no ordinary viewer UI is added. */
    int32 GetActiveDynamicChainCount() const { return ChainStates.Num(); }
    float GetLastMaxDisplacementCm() const { return LastMaxDisplacementCm; }
    uint64 GetLastSolvedFrame() const { return LastSolvedFrame; }

    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
                               FActorComponentTickFunction* ThisTickFunction) override;
    virtual void FinalizeBoneTransform() override;
    virtual FBoxSphereBounds CalcBounds(const FTransform& LocalToWorld) const override;

protected:
    virtual void OnRegister() override;
    virtual void OnUnregister() override;

private:
    UPROPERTY(Transient)
    TObjectPtr<UViewerDynamicsProfile> DynamicsProfile;

    bool bSecondaryMotionEnabled = true;
    bool bInsideFinalize = false;
    bool bStateInitialized = false;
    uint64 LastSolvedFrame = MAX_uint64;
    float LastMaxDisplacementCm = 0.f;
    TWeakObjectPtr<USkeletalMesh> CachedMesh;
    TWeakObjectPtr<USkeletalMesh> AnimatedBoundsMesh;
    FBox AnimatedLocalBounds = FBox(ForceInit);
    FTransform PreviousComponentTransform = FTransform::Identity;

    struct FChainState
    {
        int32 SettingsIndex = INDEX_NONE;
        TArray<int32> Bones;
        TArray<int32> ParentParticles;
        TArray<FVector> Positions;
        TArray<FVector> PreviousPositions;
        float Accumulator = 0.f;
    };
    TArray<FChainState> ChainStates;

    void RebuildChains(const TArray<FTransform>& AnimationPose);
    void UpdateAnimatedBounds();
    void ApplySecondaryMotion(TArray<FTransform>& Pose, float DeltaSeconds);
    void SeedChain(FChainState& State, const TArray<FVector>& Goals);
    bool SolveChain(FChainState& State, const FViewerDynamicChain& Settings,
                    const TArray<FVector>& Goals, const FTransform& ComponentTransform,
                    float DeltaSeconds);
};
