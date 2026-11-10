#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "PCSourceRecord.generated.h"

/** Editable source evidence, rather than a reconstructed shader/particle/animation. */
UCLASS(BlueprintType)
class PCASSETDATA_API UPCSourceRecord : public UDataAsset
{
    GENERATED_BODY()
public:
    virtual bool IsEditorOnly() const override { return true; }

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source") FString SourcePackage;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source") FString SourceFile;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source") FString SourceSHA256;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source") FString SourceType;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source", meta=(MultiLine=true)) FString SourceJSON;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="PC Source") TArray<TSoftObjectPtr<UObject>> ImportedAssets;
};
