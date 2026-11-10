#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "PCViewerAnimationLibrary.generated.h"

class UAnimSequence;
class USkeletalMeshComponent;

/** Adapts existing viewer idle bone tracks to independent PC mesh skeletons. */
UCLASS()
class PCASSETIMPORTER_API UPCViewerAnimationLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    /** Read-only source/target bone, reference-basis and sampling audit. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Animation")
    static FString InspectAnimationCompatibility(const FString& SourceAnimationPackage, const FString& TargetMeshPackage);

    /** Options source_kind must explicitly be same_outfit_idle, same_character_idle or shared_idle. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Animation")
    static FString AdaptAnimation(const FString& SourceAnimationPackage, const FString& TargetMeshPackage,
        const FString& TargetAnimationPackage, const FString& OptionsJson);

    /** Rechecks every raw key and every compressed frame against the source adaptation. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Animation")
    static FString VerifyAdaptedAnimation(const FString& SourceAnimationPackage, const FString& TargetMeshPackage,
        const FString& TargetAnimationPackage);

    /** Evaluates a transient preview component's exact-skeleton clip and refreshes its render pose. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Animation")
    static bool SamplePreviewPose(USkeletalMeshComponent* Component, UAnimSequence* Animation, float TimeSeconds);
};
