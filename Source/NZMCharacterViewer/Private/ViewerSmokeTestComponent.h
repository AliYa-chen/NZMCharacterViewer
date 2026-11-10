#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "ViewerSmokeTestComponent.generated.h"

class AViewerPawn;
class FJsonObject;
class FJsonValue;
class UViewerPreferences;
class UViewerSkeletalMeshComponent;
class USkeletalMeshComponent;

/** Created only by explicit development smoke-test or lighting-sweep flags. */
UCLASS(Transient)
class UViewerSmokeTestComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UViewerSmokeTestComponent();
    void Start(AViewerPawn* InViewer);
    void Stop();
    bool IsRunning() const;
    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
        FActorComponentTickFunction* ThisTickFunction) override;

protected:
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    enum class EStage : uint8 { Disabled, WaitingForLoad, Settling, RenderingCapture, LightingSweep, SettingsProbe, DrivingDynamics,
        DisabledDynamics, ReplayingDynamics, WaitingForScreenshot, Advance, Complete };
    struct FSelection { int32 Character; int32 Outfit; };
    struct FDynamicObservation
    {
        TWeakObjectPtr<UViewerSkeletalMeshComponent> Component;
        int32 PartIndex = INDEX_NONE;
        int32 MaxActiveChains = 0;
        int32 SolvedFrameChanges = 0;
        int32 Samples = 0;
        uint64 PreviousSolvedFrame = MAX_uint64;
        float PeakDisplacementCm = 0.f;
        float DrivenRotationDegrees = 0.f;
        float SafetyLimitCm = 0.f;
        FQuat PreviousRotation = FQuat::Identity;
        bool bFinite = true;
    };
    struct FAttachmentObservation
    {
        TWeakObjectPtr<USkeletalMeshComponent> Child;
        TWeakObjectPtr<USkeletalMeshComponent> Parent;
        int32 PartIndex = INDEX_NONE;
        FName Socket;
        FTransform Relative = FTransform::Identity;
        FTransform PreviousSocket = FTransform::Identity;
        int32 Samples = 0;
        double MaxPositionErrorCm = 0.0;
        double MaxRotationErrorDegrees = 0.0;
        double MaxScaleError = 0.0;
        double ParentTranslationTravelCm = 0.0;
        double ParentRotationTravelDegrees = 0.0;
        bool bFinite = true;
        bool bParentAndSocketMatch = true;
        bool bRelativeTransformMatches = true;
    };

    TWeakObjectPtr<AViewerPawn> Viewer;
    TArray<FSelection> Selections;
    TArray<TSharedPtr<FJsonValue>> Results;
    TArray<TSharedPtr<FJsonValue>> Checks;
    TSharedPtr<FJsonObject> CurrentResult;
    EStage Stage = EStage::Disabled;
    int32 CaseIndex = 0;
    int32 StableFrames = 0;
    double StageStarted = 0.0;
    double StableSince = 0.0;
    bool bScreenshotProcessed = false;
    bool bReportWriteFailed = false;
    bool bUnexpectedInterruption = false;
    FString RunDirectory;
    FString StartedUtc;
    FString ScreenshotPath;
    int64 LastScreenshotSize = -1;
    double ScreenshotSizeStableSince = 0.0;
    FDelegateHandle ScreenshotDelegate;
    UPROPERTY(Transient) TObjectPtr<UViewerPreferences> SavedPreferences;
    TMap<FString, FString> SavedConsoleVariables;
    TArray<FDynamicObservation> DynamicObservations;
    TArray<FAttachmentObservation> AttachmentObservations;
    TArray<FTransform> SceneRelativeTransforms;
    float MaxSceneTranslationErrorCm = 0.f;
    float MaxSceneRotationErrorDegrees = 0.f;
    bool bSceneStayedWithCharacter = true;
    TArray<TWeakObjectPtr<UViewerSkeletalMeshComponent>> PreviousCaseComponents;
    int32 ProbeFrames = 0;
    int32 ExpectedFrameRateChoice = INDEX_NONE;
    int32 ExpectedResolutionChoice = INDEX_NONE;
    float ExpectedFrameRate = 0.f;
    float ExpectedResolution = 0.f;
    float SavedEngineMaxFps = 0.f;
    int32 SavedFramePace = 0;
    bool bPreferencesRestored = false;
    bool bLightingSweep = false;
    bool bSavedUiEnabled = true;
    bool bLightingSweepStarted = false;
    bool bRenderingCaptureRequested = false;
    bool bProfileShowUIOverridden = false;
    bool bProfileShowUIRestored = true;
    FString SavedProfileShowUIValue;
    uint32 SavedProfileShowUIPriority = 0;
    bool bLightingGridOverridden = false;
    bool bLightingGridRestored = true;
    FString SavedLightingGridValue;
    FString LastLightingScreenshotPath;
    double NextLightingScreenshotSeconds = 0.0;
    int32 LightingScreenshotIndex = 0;
    int32 LightingScreenshotBusySkips = 0;
    TSharedPtr<FJsonObject> LightingSweepReport;
    TArray<TSharedPtr<FJsonValue>> LightingSweepSamples;
    TArray<FString> LightingSweepScreenshots;

    void BeginCase();
    void SaveUserSettings();
    void RestoreUserSettings(bool bRecord);
    void BeginRuntimeDiagnostics();
    void BeginLightingSweep();
    void RequestRenderingCapture();
    bool RestoreRenderingCaptureSettings();
    void TickLightingSweep(float DeltaTime);
    void StoreLightingSweepReport();
    void RestoreLightingGridOverride();
    void FinishSettingsProbe();
    void BeginDynamicsProbe();
    void SampleDynamics();
    void FinishDynamicsProbe();
    void BeginAttachmentProbe();
    void SampleAttachments();
    void FinishAttachmentProbe();
    void CheckAttachmentVisibility();
    void CheckResetState(const FString& Prefix);
    void CheckDisabledDynamicsPose();
    void FinishReplayProbe();
    void InspectViewportAndUi();
    void InspectCurrentSelection();
    void RequestScreenshot();
    void OnScreenshotProcessed();
    bool IsScreenshotFileReady();
    void FinishScreenshot(bool bTimedOut);
    void FinishCase();
    void Complete();
    void WriteReport(const FString& Status);
    void AddCheck(const FString& Name, const FString& Status, const FString& Detail = FString());
    FString CaseStatus() const;
};
