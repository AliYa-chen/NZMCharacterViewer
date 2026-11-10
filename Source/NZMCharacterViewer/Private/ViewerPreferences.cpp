#include "ViewerPreferences.h"
#include "ViewerGraphicsCapabilities.h"
#include "Engine/Engine.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformFramePacer.h"
#include "Kismet/GameplayStatics.h"
#include "Scalability.h"
#include "RenderUtils.h"
#include "DataDrivenShaderPlatformInfo.h"

namespace
{
    const FString Slot(TEXT("ViewerPreferences"));
    const TArray<float> ResolutionScales{70.f, 100.f, 125.f, 150.f, 200.f};

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
    if (Result->GraphicsVersion < 1)
    {
        Result->SetChoice(EViewerSetting::Quality, 3);
        Result->GraphicsVersion = 1;
    }
    if (Result->GraphicsVersion < 2)
    {
        // Restore the requested SSR pipeline after earlier diagnostic runs.
        // Later user choices remain editable and are not forced every frame.
        Result->bPostProcessing = true;
        Result->bScreenSpaceReflections = true;
        Result->Antialiasing = 0;
        Result->GraphicsVersion = 2;
    }
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

bool UViewerPreferences::UsesMobileRenderer()
{
    return IsMobilePlatform(GMaxRHIShaderPlatform);
}

TArray<int32> UViewerPreferences::AntialiasingSamples()
{
    TArray<int32> Values{1};
    const bool bSupportsMSAA = UsesMobileRenderer()
        ? !IsMobileDeferredShadingEnabled(GMaxRHIShaderPlatform)
        : IsForwardShadingEnabled(GMaxRHIShaderPlatform);
    if (bSupportsMSAA)
    {
        const TArray<int32> NativeSamples = UViewerGraphicsCapabilities::SupportedMSAASampleCounts();
        for (int32 Count : {2, 4, 8})
            if (NativeSamples.Contains(Count)) Values.Add(Count);
    }
    // TAA is also the safe migration for old MSAA choices on deferred renderers.
    Values.Add(0);
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
    if (!AntialiasingSamples().Contains(Antialiasing)) Antialiasing = 0;
    if (bScreenSpaceReflections) Antialiasing = 0;
}

void UViewerPreferences::Persist()
{
    Sanitize();
    if (!UGameplayStatics::SaveGameToSlot(this, Slot, 0))
        UE_LOG(LogTemp, Warning, TEXT("Viewer preferences could not be saved."));
}

void UViewerPreferences::ApplyEngineSettings(bool bPreserveSourceRendering)
{
    Sanitize();
    // Preserve the engine's launch defaults before any legacy stage writes an
    // artistic preset. Switching to a PC source stage must not inherit those
    // older overrides. Explicit source CVars are applied by the owning Pawn.
    static TMap<FName, float> LaunchRenderingDefaults;
    static const TCHAR* RenderingVariables[] = {
        TEXT("r.BloomQuality"), TEXT("r.DepthOfFieldQuality"), TEXT("r.Tonemapper.Sharpen"),
        TEXT("r.Tonemapper.Quality"), TEXT("r.Mobile.AmbientOcclusionQuality"),
        TEXT("r.AmbientOcclusionLevels"), TEXT("r.AmbientOcclusionMaxQuality"),
        TEXT("r.SSR.Quality"), TEXT("r.SSR.HalfResSceneColor") };
    if (LaunchRenderingDefaults.IsEmpty())
        for (const TCHAR* Name : RenderingVariables)
            if (const IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(Name))
                LaunchRenderingDefaults.Add(FName(Name), Variable->GetFloat());
    if (bPreserveSourceRendering)
        for (const auto& Value : LaunchRenderingDefaults)
            SetVariable(*Value.Key.ToString(), Value.Value);
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
    SetVariable(TEXT("r.MSAACount"), Antialiasing == 0 ? 1.f : Antialiasing);
    SetVariable(TEXT("r.Mobile.AntiAliasing"), Antialiasing == 0 ? 2.f : Antialiasing == 1 ? 0.f : 3.f);
    SetVariable(TEXT("r.AntiAliasingMethod"), Antialiasing == 0 ? 2.f : Antialiasing == 1 ? 0.f : 3.f);
    SetVariable(TEXT("r.ShadowQuality"), Shadows == 0 ? 0.f : 3.f);
    const float ShadowResolution = Shadows == 0 ? 256.f : Shadows == 1 ? 512.f : Shadows == 2 ? 2048.f : 4096.f;
    SetVariable(TEXT("r.Shadow.MaxResolution"), ShadowResolution);
    SetVariable(TEXT("r.Shadow.MaxCSMResolution"), ShadowResolution);
    SetVariable(TEXT("r.Shadow.CSM.MaxCascades"), Shadows == 0 ? 0.f : Shadows == 1 ? 1.f : Shadows == 2 ? 2.f : 4.f);
    SetVariable(TEXT("r.Shadow.MaxNumPointShadowCacheUpdatesPerFrame"), 4.f);
    SetVariable(TEXT("r.MaxAnisotropy"), Textures == 0 ? 1.f : Textures == 1 ? 4.f : Textures == 2 ? 8.f : 16.f);
    SetVariable(TEXT("r.Streaming.MipBias"), Textures == 0 ? 1.5f : Textures == 1 ? 1.f : 0.f);
    if (!bPreserveSourceRendering)
    {
        SetVariable(TEXT("r.BloomQuality"), bPostProcessing && bBloom ? 4.f : 0.f);
        SetVariable(TEXT("r.DepthOfFieldQuality"), bPostProcessing && bDepthOfField ? 2.f : 0.f);
        SetVariable(TEXT("r.Tonemapper.Sharpen"), bPostProcessing ? 0.35f : 0.f);
        SetVariable(TEXT("r.Tonemapper.Quality"), bPostProcessing ? 5.f : 0.f);
        SetVariable(TEXT("r.Mobile.AmbientOcclusionQuality"), bPostProcessing && bAmbientOcclusion ? 3.f : 0.f);
        SetVariable(TEXT("r.AmbientOcclusionLevels"), bPostProcessing && bAmbientOcclusion ? 1.f : 0.f);
        SetVariable(TEXT("r.AmbientOcclusionMaxQuality"), bPostProcessing && bAmbientOcclusion ? 100.f : 0.f);
        SetVariable(TEXT("r.SSR.Quality"), bPostProcessing && bScreenSpaceReflections && Antialiasing == 0
            ? UsesMobileRenderer() ? 2.f : 4.f : 0.f);
        SetVariable(TEXT("r.SSR.HalfResSceneColor"), 0.f);
    }
    if (!bPostProcessing || !bBloom) SetVariable(TEXT("r.BloomQuality"), 0.f);
    if (!bPostProcessing || !bDepthOfField) SetVariable(TEXT("r.DepthOfFieldQuality"), 0.f);
    if (!bPostProcessing || !bAmbientOcclusion)
    {
        SetVariable(TEXT("r.Mobile.AmbientOcclusionQuality"), 0.f);
        SetVariable(TEXT("r.AmbientOcclusionLevels"), 0.f);
    }
    if (!bPostProcessing || !bScreenSpaceReflections || Antialiasing != 0) SetVariable(TEXT("r.SSR.Quality"), 0.f);
    SetVariable(TEXT("r.Streaming.PoolSize"), Textures == 0 ? 160.f : Textures == 1 ? 256.f : Textures == 2 ? 512.f : 768.f);
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
    case EViewerSetting::Antialiasing: return FMath::Max(0, AntialiasingSamples().Find(Antialiasing));
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
            Antialiasing = Quality < 1 ? 1 : Quality < 2 ? 2 : 0;
            ResolutionPercent = Quality == 0 ? 70.f : Quality == 3 ? 200.f : 100.f;
            bPostProcessing = bBloom = bVignette = bSecondaryMotion = true;
            bAmbientOcclusion = bScreenSpaceReflections = Quality >= 2;
        }
        break;
    case EViewerSetting::Resolution:
        if (ResolutionScales.IsValidIndex(Index)) ResolutionPercent = ResolutionScales[Index];
        Quality = 4;
        break;
    case EViewerSetting::Textures: Textures = FMath::Clamp(Index, 0, 3); Quality = 4; break;
    case EViewerSetting::Shadows: Shadows = FMath::Clamp(Index, 0, 3); Quality = 4; break;
    case EViewerSetting::Antialiasing:
    {
        const TArray<int32> Samples = AntialiasingSamples();
        if (Samples.IsValidIndex(Index)) Antialiasing = Samples[Index];
        if (Antialiasing != 0) bScreenSpaceReflections = false;
        Quality = 4;
        break;
    }
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
    case EViewerSetting::AmbientOcclusion: bAmbientOcclusion = bEnabled; break;
    case EViewerSetting::ScreenSpaceReflections:
        bScreenSpaceReflections = bEnabled;
        if (bEnabled) Antialiasing = 0;
        break;
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
    case EViewerSetting::AmbientOcclusion: return bAmbientOcclusion;
    case EViewerSetting::ScreenSpaceReflections: return bScreenSpaceReflections;
    default: return false;
    }
}

void UViewerPreferences::ResetDefaults()
{
    FrameRate = 60; Quality = Textures = Shadows = 3;
    ResolutionPercent = 200.f; Antialiasing = 0;
    bPostProcessing = bBloom = bVignette = bSecondaryMotion = true;
    bDepthOfField = false;
    bAmbientOcclusion = bScreenSpaceReflections = true;
    GraphicsVersion = 2;
    Sanitize();
}
