#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "PCAssetImportLibrary.generated.h"

class UPCSourceRecord;

/** Editor-only bridge for the published CUE4Parse ActorX snapshot. */
UCLASS()
class PCASSETIMPORTER_API UPCAssetImportLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    /** Options: slot_names, source_package, source_sha256, rebuild_source_corner_layout (bool). */
    UFUNCTION(BlueprintCallable, Category="PC Assets")
    static FString ImportActorx(const FString& SourceFile, const FString& TargetPackage, const FString& OptionsJson);

    /** Checks saved source mesh data, bind skeleton, UVs, colors, triangles and built skin weights. */
    UFUNCTION(BlueprintCallable, Category="PC Assets")
    static FString VerifyActorx(const FString& SourceFile, const FString& MeshPackage);

    /** Read-only verification of active Metal shader maps; NullRHI never passes. */
    UFUNCTION(BlueprintCallable, Category="PC Assets")
    static FString VerifyMaterialShaders(const TArray<FString>& MaterialPackages);

    /** Sets serialized soft references without loading dependency assets or saving the record. */
    UFUNCTION(BlueprintCallable, Category="PC Assets")
    static bool SetSourceRecordReferences(UPCSourceRecord* Record, const TArray<FString>& AssetPackages);
};
