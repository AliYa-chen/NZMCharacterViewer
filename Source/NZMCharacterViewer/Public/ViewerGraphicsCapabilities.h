#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ViewerGraphicsCapabilities.generated.h"

/** Native Metal device queries; these do not enable any Unreal renderer feature. */
UCLASS()
class NZMCHARACTERVIEWER_API UViewerGraphicsCapabilities : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    /** JSON distinguishes native hardware capability from renderer support. */
    UFUNCTION(BlueprintPure, Category="Viewer|Graphics capabilities")
    static FString ReportJson();

    UFUNCTION(BlueprintPure, Category="Viewer|Graphics capabilities")
    static FString NativeGPUName();

    /** True only when the native MTLDevice actually reports support. */
    UFUNCTION(BlueprintPure, Category="Viewer|Graphics capabilities")
    static bool SupportsRayTracing();

    /** Native sample counts among 1, 2, 4 and 8; format/RHI limits may be lower. */
    UFUNCTION(BlueprintPure, Category="Viewer|Graphics capabilities")
    static TArray<int32> SupportedMSAASampleCounts();

    UFUNCTION(BlueprintPure, Category="Viewer|Graphics capabilities")
    static int32 MaxMSAASamples();
};
