#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ViewerAnimationImportLibrary.generated.h"

class UAnimSequence;
class USkeletalMesh;

/** Editor import bridge for decoded local bone curves; no runtime file import. */
UCLASS()
class NZMCHARACTERVIEWER_API UViewerAnimationImportLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="Viewer|Import")
    static bool PopulateAnimationFromCurves(UAnimSequence* Animation, USkeletalMesh* TargetMesh,
        const FString& JsonFile, FString& Report);
};
