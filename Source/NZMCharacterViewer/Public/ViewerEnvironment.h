#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "Engine/Scene.h"
#include "ViewerEnvironment.generated.h"

class UTextureCube;
class UStaticMesh;
class UMaterialInterface;

USTRUCT(BlueprintType)
struct FViewerEnvironmentLight
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FName Id;
    UPROPERTY(EditAnywhere) FName Kind;
    UPROPERTY(EditAnywhere) FVector Location = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FRotator Rotation = FRotator::ZeroRotator;
    UPROPERTY(EditAnywhere) FLinearColor Color = FLinearColor::White;
    UPROPERTY(EditAnywhere) float Intensity = 1.f;
    UPROPERTY(EditAnywhere) float Radius = 1000.f;
    UPROPERTY(EditAnywhere) float InnerCone = 3.f;
    UPROPERTY(EditAnywhere) float OuterCone = 15.f;
    UPROPERTY(EditAnywhere) float SourceRadius = 0.f;
    UPROPERTY(EditAnywhere) float SourceLength = 0.f;
    UPROPERTY(EditAnywhere) float Temperature = 6500.f;
    UPROPERTY(EditAnywhere) bool bUseTemperature = false;
    UPROPERTY(EditAnywhere) bool bCastShadows = false;
    UPROPERTY(EditAnywhere) bool bInverseSquaredFalloff = true;
    UPROPERTY(EditAnywhere) float FalloffExponent = 8.f;
    UPROPERTY(EditAnywhere) float SpecularScale = 1.f;
    UPROPERTY(EditAnywhere) float ShadowBias = 0.5f;
    UPROPERTY(EditAnywhere) int32 IntensityUnits = 1;
};

USTRUCT(BlueprintType)
struct FViewerEnvironmentMesh
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FName Id;
    UPROPERTY(EditAnywhere) TSoftObjectPtr<UStaticMesh> Mesh;
    UPROPERTY(EditAnywhere) TArray<TSoftObjectPtr<UMaterialInterface>> Materials;
    UPROPERTY(EditAnywhere) FTransform Transform = FTransform::Identity;
    UPROPERTY(EditAnywhere) bool bCastShadows = false;
};

UCLASS(BlueprintType)
class NZMCHARACTERVIEWER_API UViewerEnvironment : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) TSoftObjectPtr<UTextureCube> Cubemap;
    UPROPERTY(EditAnywhere) float SkyIntensity = 1.f;
    UPROPERTY(EditAnywhere) FLinearColor SkyColor = FLinearColor::White;
    UPROPERTY(EditAnywhere) TArray<FViewerEnvironmentLight> Lights;
    UPROPERTY(EditAnywhere) TArray<FViewerEnvironmentMesh> Meshes;
    UPROPERTY(EditAnywhere) FPostProcessSettings PostProcess;
    UPROPERTY(EditAnywhere) float CameraHorizontalFOV = 29.9281126f;
    UPROPERTY(EditAnywhere) float ReferenceAspectRatio = 1.7777778f;
    UPROPERTY(EditAnywhere) float CameraDistance = 350.f;
    UPROPERTY(EditAnywhere) float CameraHeight = 102.f;
    UPROPERTY(EditAnywhere) float CameraPitch = 4.4f;
    UPROPERTY(EditAnywhere) float CameraYaw = -90.f;
    UPROPERTY(EditAnywhere) bool bUseSourceCamera = false;
    UPROPERTY(EditAnywhere) FTransform SourceCameraTransform = FTransform::Identity;
    // Custom stages share the source mesh origin with their furniture and
    // scenery. Bounds normalization must not move the avatar away from it.
    UPROPERTY(EditAnywhere) bool bPreserveModelOrigin = false;
    // Explicit viewer framing fallback for seated source poses whose dynamic
    // original camera route is absent from the recovered cooked properties.
    UPROPERTY(EditAnywhere) bool bFrameFromHeadBone = false;
    UPROPERTY(EditAnywhere) FString ReconstructionNotes;
};
