#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "PCViewerClothLibrary.generated.h"

class USkeletalMeshComponent;

/** Editor authoring and focused diagnostics for real Chaos vertex cloth. */
UCLASS()
class PCASSETIMPORTER_API UPCViewerClothLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    /** Duplicate a converted mesh; import sourced physical meshes and bind exact source sections. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Cloth")
    static FString BuildClothMesh(const FString& SourceMeshPackage, const FString& TargetMeshPackage,
        const FString& OptionsJson);

    /** Inspect saved native clothing assets, pin masks, render mappings, and fitted collision bodies. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Cloth")
    static FString InspectClothMesh(const FString& MeshPackage);

    /** Inspect actual solver instances and simulated positions after the component has ticked. */
    UFUNCTION(BlueprintCallable, Category="PC Viewer Cloth")
    static FString InspectClothComponent(USkeletalMeshComponent* Component);
};
