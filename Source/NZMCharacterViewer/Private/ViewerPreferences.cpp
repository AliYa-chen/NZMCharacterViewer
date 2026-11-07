#include "ViewerPreferences.h"
#include "Engine/Engine.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformFramePacer.h"
#include "Kismet/GameplayStatics.h"
#include "Scalability.h"

namespace
{
    const FString Slot(TEXT("ViewerPreferences"));
    const TArray<float> ResolutionScales{70.f, 100.f, 125.f, 150.f, 200.f};
    const TArray<int32> Samples{1, 2, 4, 8};

    void SetVariable(const TCHAR* Name, float Value)
    {
        if (IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(Name))
            Variable->Set(Value, ECVF_SetByGameOverride);
    }
}

UViewerPreferences* UViewerPreferences::Load()
{
    UViewerPreferences* Result = Cast<UViewerPreferences>(UGameplayStatics::LoadGameFromSlot(Slot, 0));
    if (!Result) Result = Cast<UViewerPreferences>(UGameplayStatics::CreateSaveGameObject(StaticClass()));
    Result->Sanitize();
    return Result;
}

TArray<int32> UViewerPreferences::FrameRates()
{
    TArray<int32> Values;
    const int32 Maximum = FMath::Max(30, FPlatformMisc::GetMaxRefreshRate());
    for (int32 Value : {30, 60, 90, 120})
        if (Value <= Maximum && FPlatformRHIFramePacer::SupportsFramePace(Value)) Values.Add(Value);
    if (Values.IsEmpty()) Values.Add(30);
    return Values;
}

void UViewerPreferences::Sanitize()
{
    const TArray<int32> Rates = FrameRates();
    if (!Rates.Contains(FrameRate)) FrameRate = Rates.Last();
    Quality = FMath::Clamp(Quality, 0, 4);
    Textures = FMath::Clamp(Textures, 0, 3);
    Shadows = FMath::Clamp(Shadows, 0, 3);
    ResolutionPercent = FMath::Clamp(ResolutionPercent, 70.f, 200.f);
    if (!Samples.Contains(Antialiasing)) Antialiasing = 4;
}

void UViewerPreferences::Persist()
{
    Sanitize();
    if (!UGameplayStatics::SaveGameToSlot(this, Slot, 0))
        UE_LOG(LogTemp, Warning, TEXT("Viewer preferences could not be saved."));
}

void UViewerPreferences::ApplyEngineSettings()
{
    Sanitize();
    Scalability::FQualityLevels Levels;
    Levels.SetFromSingleQualityLevel(FMath::Min(Quality, 3));
    Levels.TextureQuality = Textures;
    Levels.ShadowQuality = Shadows;
    Levels.ResolutionQuality = ResolutionPercent;
    Scalability::SetQualityLevels(Levels);
    if (GEngine) GEngine->SetMaxFPS(static_cast<float>(FrameRate));
    FPlatformRHIFramePacer::SetFramePace(FrameRate);
    SetVariable(TEXT("t.MaxFPS"), FrameRate);
    SetVariable(TEXT("r.ScreenPercentage"), ResolutionPercent);
    SetVariable(TEXT("r.MSAACount"), Antialiasing);
    SetVariable(TEXT("r.ShadowQuality"), Shadows == 0 ? 0.f : 3.f);
    SetVariable(TEXT("r.Shadow.MaxResolution"), Shadows == 0 ? 256.f : Shadows == 1 ? 512.f : Shadows == 2 ? 1024.f : 2048.f);
    SetVariable(TEXT("r.Shadow.CSM.MaxCascades"), Shadows == 0 ? 0.f : Shadows == 1 ? 1.f : 2.f);
    SetVariable(TEXT("r.MaxAnisotropy"), Textures == 0 ? 1.f : Textures == 1 ? 4.f : Textures == 2 ? 8.f : 16.f);
    SetVariable(TEXT("r.Streaming.MipBias"), Textures == 0 ? 1.5f : Textures == 1 ? 1.f : 0.f);
    SetVariable(TEXT("r.BloomQuality"), bPostProcessing && bBloom ? 4.f : 0.f);
    SetVariable(TEXT("r.DepthOfFieldQuality"), bPostProcessing && bDepthOfField ? 2.f : 0.f);
    SetVariable(TEXT("r.Tonemapper.Sharpen"), bPostProcessing ? 0.35f : 0.f);
    SetVariable(TEXT("r.Streaming.PoolSize"), Textures == 0 ? 160.f : Textures == 1 ? 256.f : Textures == 2 ? 384.f : 512.f);
}

int32 UViewerPreferences::GetChoice(EViewerSetting Setting) const
{
    switch (Setting)
    {
    case EViewerSetting::FrameRate: return FMath::Max(0, FrameRates().Find(FrameRate));
    case EViewerSetting::Quality: return Quality;
    case EViewerSetting::Resolution:
        for (int32 Index = 0; Index < ResolutionScales.Num(); ++Index)
            if (FMath::IsNearlyEqual(ResolutionScales[Index], ResolutionPercent)) return Index;
        return 1;
    case EViewerSetting::Textures: return Textures;
    case EViewerSetting::Shadows: return Shadows;
    case EViewerSetting::Antialiasing: return FMath::Max(0, Samples.Find(Antialiasing));
    default: return 0;
    }
}

void UViewerPreferences::SetChoice(EViewerSetting Setting, int32 Index)
{
    switch (Setting)
    {
    case EViewerSetting::FrameRate:
        if (FrameRates().IsValidIndex(Index)) FrameRate = FrameRates()[Index];
        break;
    case EViewerSetting::Quality:
        if (Index == 4) Quality = 4;
        if (Index < 4)
        {
            Quality = FMath::Clamp(Index, 0, 3);
            Textures = Shadows = Quality;
            Antialiasing = Quality < 1 ? 1 : Quality < 2 ? 2 : Quality < 3 ? 4 : 8;
            ResolutionPercent = Quality == 0 ? 70.f : Quality == 3 ? 125.f : 100.f;
        }
        break;
    case EViewerSetting::Resolution:
        if (ResolutionScales.IsValidIndex(Index)) ResolutionPercent = ResolutionScales[Index];
        Quality = 4;
        break;
    case EViewerSetting::Textures: Textures = FMath::Clamp(Index, 0, 3); Quality = 4; break;
    case EViewerSetting::Shadows: Shadows = FMath::Clamp(Index, 0, 3); Quality = 4; break;
    case EViewerSetting::Antialiasing:
        if (Samples.IsValidIndex(Index)) Antialiasing = Samples[Index];
        Quality = 4;
        break;
    default: break;
    }
    Sanitize();
}

void UViewerPreferences::SetToggle(EViewerSetting Setting, bool bEnabled)
{
    switch (Setting)
    {
    case EViewerSetting::PostProcessing: bPostProcessing = bEnabled; break;
    case EViewerSetting::Bloom: bBloom = bEnabled; break;
    case EViewerSetting::Vignette: bVignette = bEnabled; break;
    case EViewerSetting::DepthOfField: bDepthOfField = bEnabled; break;
    case EViewerSetting::SecondaryMotion: bSecondaryMotion = bEnabled; break;
    default: break;
    }
}

bool UViewerPreferences::GetToggle(EViewerSetting Setting) const
{
    switch (Setting)
    {
    case EViewerSetting::PostProcessing: return bPostProcessing;
    case EViewerSetting::Bloom: return bBloom;
    case EViewerSetting::Vignette: return bVignette;
    case EViewerSetting::DepthOfField: return bDepthOfField;
    case EViewerSetting::SecondaryMotion: return bSecondaryMotion;
    default: return false;
    }
}

void UViewerPreferences::ResetDefaults()
{
    FrameRate = 60; Quality = Textures = Shadows = 2;
    ResolutionPercent = 100.f; Antialiasing = 4;
    bPostProcessing = bBloom = bVignette = bSecondaryMotion = true;
    bDepthOfField = false;
    Sanitize();
}
