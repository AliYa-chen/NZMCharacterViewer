#pragma once

#include "CoreMinimal.h"
#include "GameFramework/SaveGame.h"
#include "ViewerPreferences.generated.h"

UENUM()
enum class EViewerSetting : uint8
{
    FrameRate, Quality, Resolution, Textures, Shadows, Antialiasing,
    PostProcessing, Bloom, Vignette, DepthOfField, SecondaryMotion
};

/** Small, device-local preferences; source materials and assets stay intact. */
UCLASS()
class NZMCHARACTERVIEWER_API UViewerPreferences : public USaveGame
{
    GENERATED_BODY()
public:
    UPROPERTY(SaveGame) int32 FrameRate = 60;
    UPROPERTY(SaveGame) int32 Quality = 2;
    UPROPERTY(SaveGame) float ResolutionPercent = 100.f;
    UPROPERTY(SaveGame) int32 Textures = 2;
    UPROPERTY(SaveGame) int32 Shadows = 2;
    UPROPERTY(SaveGame) int32 Antialiasing = 4;
    UPROPERTY(SaveGame) bool bPostProcessing = true;
    UPROPERTY(SaveGame) bool bBloom = true;
    UPROPERTY(SaveGame) bool bVignette = true;
    UPROPERTY(SaveGame) bool bDepthOfField = false;
    UPROPERTY(SaveGame) bool bSecondaryMotion = true;

    static UViewerPreferences* Load();
    static TArray<int32> FrameRates();
    void Persist();
    void ApplyEngineSettings();
    void SetChoice(EViewerSetting Setting, int32 Index);
    int32 GetChoice(EViewerSetting Setting) const;
    void SetToggle(EViewerSetting Setting, bool bEnabled);
    bool GetToggle(EViewerSetting Setting) const;
    void ResetDefaults();
    void Sanitize();
};
