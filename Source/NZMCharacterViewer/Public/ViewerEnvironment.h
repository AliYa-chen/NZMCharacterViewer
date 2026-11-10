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
    // Bit 0 lights the shared stage; bit 1 can isolate character fill lights.
    UPROPERTY(EditAnywhere) int32 LightingChannelMask = 1;
    // Source-mode fields are explicit so runtime construction does not replace
    // them with the legacy viewer's stage-wide lighting calibration.
    UPROPERTY(EditAnywhere) int32 Mobility = 2;
    UPROPERTY(EditAnywhere) bool bVisible = true;
    UPROPERTY(EditAnywhere) bool bAffectsWorld = true;
    UPROPERTY(EditAnywhere) FVector Scale = FVector::OneVector;
    UPROPERTY(EditAnywhere) float ShadowResolutionScale = 1.f;
    UPROPERTY(EditAnywhere) float DynamicShadowDistance = 40000.f;
    UPROPERTY(EditAnywhere) int32 DynamicShadowCascades = 4;
    UPROPERTY(EditAnywhere) float CascadeDistributionExponent = 3.f;
    UPROPERTY(EditAnywhere) float CascadeTransitionFraction = 0.1f;
    UPROPERTY(EditAnywhere) float SourceWidth = 64.f;
    UPROPERTY(EditAnywhere) float SourceHeight = 64.f;
    UPROPERTY(EditAnywhere) float BarnDoorAngle = 88.f;
    UPROPERTY(EditAnywhere) float BarnDoorLength = 20.f;
    UPROPERTY(EditAnywhere) float IndirectLightingIntensity = 1.f;
    UPROPERTY(EditAnywhere) float VolumetricScatteringIntensity = 1.f;
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
    UPROPERTY(EditAnywhere, Category = "PC source") bool bUseSourceRendering = false;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bHasMobilePostProcess = false;
    UPROPERTY(EditAnywhere, Category = "PC source") FPostProcessSettings MobilePostProcess;
    UPROPERTY(EditAnywhere, Category = "PC source") TMap<FName, float> SourceConsoleVariables;
    UPROPERTY(EditAnywhere, Category = "PC source") TMap<FName, float> MobileSourceConsoleVariables;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bSkyCastShadows = false;
    UPROPERTY(EditAnywhere, Category = "PC source") float SkyCubemapAngle = 0.f;
    UPROPERTY(EditAnywhere, Category = "PC source") float SkyVolumetricScatteringIntensity = 1.f;
    UPROPERTY(EditAnywhere, Category = "PC source") int32 CharacterLightingChannelMask = 1;
    // Alignment of the avatar to retained stage geometry. This is separate
    // from per-part source attachment transforms and source camera evidence.
    UPROPERTY(EditAnywhere, Category = "PC source") FTransform ModelTransform = FTransform::Identity;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bSourcePostEnabled = true;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bSourcePostUnbound = true;
    UPROPERTY(EditAnywhere, Category = "PC source") float SourcePostPriority = 0.f;
    UPROPERTY(EditAnywhere, Category = "PC source") float SourcePostBlendWeight = 1.f;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bUseSourceFog = false;
    UPROPERTY(EditAnywhere, Category = "PC source") FTransform FogTransform = FTransform::Identity;
    UPROPERTY(EditAnywhere, Category = "PC source") float FogMaxOpacity = 1.f;
    UPROPERTY(EditAnywhere, Category = "PC source") bool bSourceVolumetricFog = false;
    UPROPERTY(EditAnywhere, Category = "PC source") float FogVolumetricScatteringDistribution = 0.2f;
    UPROPERTY(EditAnywhere, Category = "PC source") float FogVolumetricDistance = 6000.f;
    // Native desktop fog multiple scattering; mobile stages never create it.
    UPROPERTY(EditAnywhere, Category = "Atmosphere") bool bEnableFogScattering = false;
    UPROPERTY(EditAnywhere, Category = "Atmosphere", meta = (EditCondition = "bEnableFogScattering", ClampMin = "0", ClampMax = "0.05"))
    float FogDensity = 0.003f;
    UPROPERTY(EditAnywhere, Category = "Atmosphere", meta = (EditCondition = "bEnableFogScattering", ClampMin = "0.001", ClampMax = "2"))
    float FogHeightFalloff = 0.2f;
    UPROPERTY(EditAnywhere, Category = "Atmosphere", meta = (EditCondition = "bEnableFogScattering", ClampMin = "0", Units = "cm"))
    float FogStartDistance = 100.f;
    UPROPERTY(EditAnywhere, Category = "Atmosphere", meta = (EditCondition = "bEnableFogScattering", HideAlphaChannel))
    FLinearColor FogColor = FLinearColor(0.04f, 0.055f, 0.075f);
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
