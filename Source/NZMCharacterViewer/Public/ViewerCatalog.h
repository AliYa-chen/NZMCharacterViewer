#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "ViewerCatalog.generated.h"

class USkeletalMesh;
class UMaterialInterface;
class UAnimSequence;
class UTexture2D;
class UViewerDynamicsProfile;
class UViewerEnvironment;

USTRUCT(BlueprintType)
struct FViewerPart
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName Id;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSoftObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TArray<TSoftObjectPtr<UMaterialInterface>> Materials;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FTransform RelativeTransform = FTransform::Identity;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) bool bVisibleByDefault = true;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSoftObjectPtr<UAnimSequence> Animation;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSoftObjectPtr<UViewerDynamicsProfile> Dynamics;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) bool bAnimationLoop = true;
};

USTRUCT(BlueprintType)
struct FViewerOutfit
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName Id;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TArray<FViewerPart> Parts;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSoftObjectPtr<UViewerEnvironment> Environment;
};

USTRUCT(BlueprintType)
struct FViewerCharacter
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName Id;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSoftObjectPtr<UTexture2D> Portrait;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TArray<FViewerOutfit> Outfits;
};

UCLASS(BlueprintType)
class NZMCHARACTERVIEWER_API UViewerCatalog : public UDataAsset
{
    GENERATED_BODY()

public:
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TArray<FViewerCharacter> Characters;
};
