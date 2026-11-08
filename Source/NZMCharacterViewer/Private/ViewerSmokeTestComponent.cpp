#include "ViewerSmokeTestComponent.h"
#include "ViewerPawn.h"
#include "ViewerWidget.h"
#include "ViewerDynamics.h"
#include "ViewerPreferences.h"
#include "ViewerEnvironment.h"
#include "ViewerGraphicsCapabilities.h"
#include "Blueprint/SlateBlueprintLibrary.h"
#include "Blueprint/WidgetTree.h"
#include "Camera/CameraComponent.h"
#include "Components/Border.h"
#include "Components/PanelWidget.h"
#include "Components/PostProcessComponent.h"
#include "Components/ScrollBox.h"
#include "Components/SafeZone.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/LightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "DynamicRHI.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StreamableManager.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformFramePacer.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/CoreMisc.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "RHIGlobals.h"
#include "RHIStrings.h"
#include "SceneInterface.h"
#include "UnrealClient.h"
#include "ReferenceSkeleton.h"
#include "RenderUtils.h"
#if WITH_EDITOR
#include "AssetCompilingManager.h"
#endif

namespace ViewerSmokeTest
{
    constexpr double LoadTimeout = 120.0;
    constexpr double SettleTimeout = 120.0;
    constexpr double SettleSeconds = 2.0;
    constexpr double ScreenshotTimeout = 20.0;
    constexpr double LightingStillSeconds = 1.5;
    constexpr double LightingCycleSeconds = 6.0;
    constexpr double LightingMotionSeconds = LightingCycleSeconds * 2.0;
    constexpr double LightingStopSeconds = 1.0;
    constexpr double LightingTotalSeconds = LightingStillSeconds + LightingMotionSeconds + LightingStopSeconds;
    constexpr double LightingCaptureInterval = 0.3;

    bool FiniteVector(const FVector& Value)
    {
        return FMath::IsFinite(Value.X) && FMath::IsFinite(Value.Y) && FMath::IsFinite(Value.Z);
    }

    TArray<TSharedPtr<FJsonValue>> VectorValues(const FVector& Value)
    {
        TArray<TSharedPtr<FJsonValue>> Values;
        for (double Number : { Value.X, Value.Y, Value.Z })
            Values.Add(FMath::IsFinite(Number)
                ? StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNumber>(Number))
                : StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNull>()));
        return Values;
    }
}

UViewerSmokeTestComponent::UViewerSmokeTestComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.bStartWithTickEnabled = false;
    PrimaryComponentTick.TickGroup = TG_PostUpdateWork;
}

bool UViewerSmokeTestComponent::IsRunning() const
{
    return Stage != EStage::Disabled && Stage != EStage::Complete;
}

void UViewerSmokeTestComponent::Start(AViewerPawn* InViewer)
{
    if (!InViewer || !GetWorld() || !GetWorld()->IsGameWorld()) return;
    Viewer = InViewer;
    bLightingSweep = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingSweep"));
    SaveUserSettings();
    if (InViewer->ViewerUI)
    {
        // Native Slate input must not change modal state or graphics choices
        // during either automated run. Production actions remain callable.
        bSavedUiEnabled = InViewer->ViewerUI->GetIsEnabled();
        InViewer->ViewerUI->SetIsEnabled(false);
    }
    StartedUtc = FDateTime::UtcNow().ToIso8601();
    const FString RunName = FString::Printf(TEXT("Run_%s_%u"),
        *FDateTime::UtcNow().ToString(TEXT("%Y%m%dT%H%M%S")), FPlatformProcess::GetCurrentProcessId());
    RunDirectory = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("ViewerSmokeTest"), RunName));
    if (!IFileManager::Get().MakeDirectory(*RunDirectory, true))
    {
        UE_LOG(LogTemp, Error, TEXT("ViewerSmokeTest cannot create %s"), *RunDirectory);
        bReportWriteFailed = true;
    }
    ScreenshotDelegate = FScreenshotRequest::OnScreenshotRequestProcessed().AddUObject(this,
        &UViewerSmokeTestComponent::OnScreenshotProcessed);
    if (InViewer->Catalog)
    {
        int32 FilterCharacter = bLightingSweep ? 0 : INDEX_NONE, FilterOutfit = bLightingSweep ? 0 : INDEX_NONE;
        FParse::Value(FCommandLine::Get(), TEXT("ViewerSmokeCharacter="), FilterCharacter);
        FParse::Value(FCommandLine::Get(), TEXT("ViewerSmokeOutfit="), FilterOutfit);
        if (bLightingSweep)
        {
            if (InViewer->Catalog->Characters.IsValidIndex(FilterCharacter)
                && InViewer->Catalog->Characters[FilterCharacter].Outfits.IsValidIndex(FilterOutfit))
                Selections.Add({ FilterCharacter, FilterOutfit });
        }
        else
            for (int32 Character = 0; Character < InViewer->Catalog->Characters.Num(); ++Character)
                for (int32 Outfit = 0; Outfit < InViewer->Catalog->Characters[Character].Outfits.Num(); ++Outfit)
                    if ((FilterCharacter == INDEX_NONE || Character == FilterCharacter)
                        && (FilterOutfit == INDEX_NONE || Outfit == FilterOutfit))
                        Selections.Add({ Character, Outfit });
    }
    if (Selections.IsEmpty())
    {
        CurrentResult = MakeShared<FJsonObject>();
        AddCheck(TEXT("runtime_catalog_has_character_outfit_pairs"), TEXT("fail"), TEXT("Catalog missing or contains no outfits."));
        FinishCase();
        Complete();
        return;
    }
    SetComponentTickEnabled(true);
    UE_LOG(LogTemp, Display, TEXT("ViewerSmokeTest starting %d real Catalog selections. Report: %s"),
        Selections.Num(), *FPaths::Combine(RunDirectory, TEXT("Report.json")));
    BeginCase();
}

void UViewerSmokeTestComponent::BeginCase()
{
    if (!Viewer.IsValid() || !Selections.IsValidIndex(CaseIndex)) { Complete(); return; }
    AViewerPawn* Pawn = Viewer.Get();
    const FSelection Selection = Selections[CaseIndex];
    CurrentResult = MakeShared<FJsonObject>();
    Checks.Reset();
    const FViewerCharacter& Character = Pawn->Catalog->Characters[Selection.Character];
    const FViewerOutfit& Outfit = Character.Outfits[Selection.Outfit];
    CurrentResult->SetNumberField(TEXT("character_index"), Selection.Character);
    CurrentResult->SetStringField(TEXT("character_id"), Character.Id.ToString());
    CurrentResult->SetStringField(TEXT("character_name"), Character.DisplayName.ToString());
    CurrentResult->SetNumberField(TEXT("outfit_index"), Selection.Outfit);
    CurrentResult->SetStringField(TEXT("outfit_id"), Outfit.Id.ToString());
    CurrentResult->SetStringField(TEXT("outfit_name"), Outfit.DisplayName.ToString());
    CurrentResult->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
    CurrentResult->SetNumberField(TEXT("expected_parts"), Outfit.Parts.Num());
    bPreferencesRestored = false;
    PreviousCaseComponents.Reset();
    for (USkeletalMeshComponent* Part : Pawn->Parts)
        if (UViewerSkeletalMeshComponent* Dynamic = Cast<UViewerSkeletalMeshComponent>(Part))
            PreviousCaseComponents.Add(Dynamic);
    const int32 PreviousGeneration = Pawn->SelectionGeneration;
    // Exercise the same public selection path as the UI. Its normal generation
    // guard cancels any intermediate outfit request when changing character.
    Pawn->SelectCharacter(Selection.Character);
    Pawn->SelectOutfit(Selection.Outfit);
    CurrentResult->SetNumberField(TEXT("selection_generation"), Pawn->SelectionGeneration);
    if (Pawn->SelectionGeneration != PreviousGeneration)
    {
        bool OldComponentsRetained = true;
        for (const TWeakObjectPtr<UViewerSkeletalMeshComponent>& Old : PreviousCaseComponents)
        {
            OldComponentsRetained &= Old.IsValid() && Old->IsRegistered();
        }
        AddCheck(TEXT("selection_switch_previous_retained_while_loading"), PreviousCaseComponents.IsEmpty()
            ? TEXT("not_applicable") : OldComponentsRetained ? TEXT("pass") : TEXT("fail"));
    }
    else AddCheck(TEXT("selection_switch_previous_components_released"), TEXT("not_applicable"), TEXT("Initial already-selected Catalog pair."));
    Stage = EStage::WaitingForLoad;
    StageStarted = FPlatformTime::Seconds();
    WriteReport(TEXT("running"));
}

void UViewerSmokeTestComponent::TickComponent(float DeltaTime, ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    if (!IsRunning()) return;
    if (!Viewer.IsValid()) { Stop(); return; }
    AViewerPawn* Pawn = Viewer.Get();
    const double Now = FPlatformTime::Seconds();
    if (Stage == EStage::Advance)
    {
        if (Now - StageStarted < 0.25) return;
        if (++CaseIndex < Selections.Num()) BeginCase();
        else Complete();
        return;
    }
    if (Stage == EStage::WaitingForScreenshot)
    {
        // The processed event can precede async image-write completion. Wait
        // for a stable nonzero PNG with its final IEND chunk on disk as well.
        if (IsScreenshotFileReady() && bScreenshotProcessed) FinishScreenshot(false);
        else if (Now - StageStarted > ViewerSmokeTest::ScreenshotTimeout) FinishScreenshot(true);
        return;
    }
    const FSelection Selection = Selections[CaseIndex];
    if (Pawn->GetCharacterIndex() != Selection.Character || Pawn->GetOutfitIndex() != Selection.Outfit)
    {
        AddCheck(TEXT("selection_remained_requested_pair"), TEXT("fail"), FString::Printf(
            TEXT("Requested %d/%d; actual %d/%d. Selection changed before this case completed."),
            Selection.Character, Selection.Outfit, Pawn->GetCharacterIndex(), Pawn->GetOutfitIndex()));
        AddCheck(TEXT("screenshot_saved"), TEXT("unknown"), TEXT("Case interrupted before capture."));
        FinishCase();
        return;
    }
    if (Stage == EStage::LightingSweep)
    {
        TickLightingSweep(DeltaTime);
        return;
    }
    if (Stage == EStage::RenderingCapture)
    {
        // ProfileGPU records an upcoming rendered frame. Let that frame finish
        // before the ordinary smoke probe changes FPS and resolution.
        if (++ProbeFrames >= 3 && Now - StageStarted >= 0.1) BeginRuntimeDiagnostics();
        return;
    }
    if (Stage == EStage::SettingsProbe)
    {
        if (++ProbeFrames >= 4 && Now - StageStarted >= 0.15)
        {
            FinishSettingsProbe();
            BeginDynamicsProbe();
        }
        return;
    }
    if (Stage == EStage::DrivingDynamics)
    {
        SampleDynamics();
        const double Elapsed = Now - StageStarted;
        const float Phase = FMath::Clamp(static_cast<float>(Elapsed / 1.2), 0.f, 1.f);
        // Exercise the real model-turn path. Per-frame changes remain well below
        // the profile's teleport threshold; the normal Pawn Tick applies DisplayYaw.
        Pawn->Yaw = 32.f * FMath::Sin(Phase * 2.f * PI);
        if ((Elapsed >= 1.2 && ++ProbeFrames >= 12) || Elapsed > 5.0)
        {
            FinishDynamicsProbe();
            Pawn->Yaw = Pawn->DisplayYaw;
            if (Pawn->Preferences)
            {
                Pawn->Preferences->SetToggle(EViewerSetting::SecondaryMotion, false);
                Pawn->ApplyPreferences(); // Deliberately bypasses Persist.
            }
            CheckResetState(TEXT("disable_immediate"));
            Stage = EStage::DisabledDynamics;
            StageStarted = Now;
            ProbeFrames = 0;
        }
        return;
    }
    if (Stage == EStage::DisabledDynamics)
    {
        if (++ProbeFrames >= 4 && Now - StageStarted >= 0.2)
        {
            CheckDisabledDynamicsPose();
            if (Pawn->Preferences)
            {
                Pawn->Preferences->SetToggle(EViewerSetting::SecondaryMotion, true);
                Pawn->ApplyPreferences();
            }
            Pawn->ReplayAnimation();
            CheckResetState(TEXT("replay_immediate"));
            const FViewerOutfit* Outfit = Pawn->GetCurrentOutfit();
            if (Outfit)
                for (int32 Index = 0; Index < Pawn->Parts.Num() && Index < Outfit->Parts.Num(); ++Index)
                    if (!Outfit->Parts[Index].Animation.IsNull() && Pawn->Parts[Index])
                    {
                        const float Position = Pawn->Parts[Index]->GetPosition();
                        AddCheck(FString::Printf(TEXT("replay.part[%d].position_reset"), Index),
                            FMath::IsFinite(Position) && FMath::Abs(Position) < 0.01f ? TEXT("pass") : TEXT("fail"));
                    }
            Stage = EStage::ReplayingDynamics;
            StageStarted = Now;
            ProbeFrames = 0;
        }
        return;
    }
    if (Stage == EStage::ReplayingDynamics)
    {
        if (++ProbeFrames >= 4 && Now - StageStarted >= 0.2)
        {
            FinishReplayProbe();
            InspectCurrentSelection();
            InspectViewportAndUi();
            RequestScreenshot();
        }
        return;
    }
    if (Stage == EStage::WaitingForLoad)
    {
        for (USkeletalMeshComponent* Preparing : Pawn->PreparingParts)
            if (Preparing && Preparing->IsVisible())
            {
                AddCheck(TEXT("preparing_character_stays_hidden"), TEXT("fail"), TEXT("Unprepared component became visible."));
                FinishCase();
                return;
            }
        const FViewerOutfit* Outfit = Pawn->GetCurrentOutfit();
        if (!Pawn->LoadingHandle.IsValid())
        {
            const bool CompleteParts = Outfit && Outfit->Parts.Num() > 0 && Pawn->Parts.Num() == Outfit->Parts.Num();
            AddCheck(TEXT("asynchronous_selection_load_completed"), CompleteParts ? TEXT("pass") : TEXT("fail"),
                Pawn->GetStatusLabel().ToString());
            AddCheck(TEXT("selection_render_resources_ready_before_visibility"), Pawn->bLastSelectionReadyBeforeVisible
                && !Pawn->bPreparingSelection ? TEXT("pass") : TEXT("fail"),
                TEXT("Actual readiness gate covers mesh/shader completion, streamed textures, PSO completion and an RHI submission fence."));
            CurrentResult->SetNumberField(TEXT("preparation_seconds"), Pawn->LastSelectionPreparationSeconds);
            bool PreviousReleased = true;
            for (const auto& Previous : PreviousCaseComponents)
                PreviousReleased &= !Previous.IsValid() || !Previous->IsRegistered();
            AddCheck(TEXT("previous_character_released_after_preparation"), PreviousCaseComponents.IsEmpty()
                ? TEXT("not_applicable") : PreviousReleased ? TEXT("pass") : TEXT("fail"));
            Stage = EStage::Settling;
            StageStarted = StableSince = Now;
            StableFrames = 0;
        }
        else if (Now - StageStarted > ViewerSmokeTest::LoadTimeout)
        {
            AddCheck(TEXT("asynchronous_selection_load_completed"), TEXT("fail"), TEXT("120 second load timeout."));
            InspectCurrentSelection();
            InspectViewportAndUi();
            AddCheck(TEXT("driven_dynamic_integration"), TEXT("unknown"), TEXT("Load timed out before a safe dynamics/settings probe."));
            RequestScreenshot();
        }
        return;
    }
    if (Stage == EStage::Settling)
    {
        int32 RemainingCompilations = 0;
#if WITH_EDITOR
        RemainingCompilations = FAssetCompilingManager::Get().GetNumRemainingAssets();
#endif
        const bool InterfaceReady = Pawn->ViewerUI && Pawn->ViewerUI->IsInViewport() && Pawn->ViewerUI->IsVisible();
        if (RemainingCompilations > 0 || !InterfaceReady)
        {
            StableSince = Now;
            StableFrames = 0;
        }
        else ++StableFrames;
        const bool TimedOut = Now - StageStarted > ViewerSmokeTest::SettleTimeout;
        if ((Now - StableSince >= ViewerSmokeTest::SettleSeconds && StableFrames >= 20) || TimedOut)
        {
            AddCheck(TEXT("assets_and_ui_settled"), TimedOut ? TEXT("unknown") : TEXT("pass"),
                FString::Printf(TEXT("Remaining editor asset compilations: %d; stable frames: %d."), RemainingCompilations, StableFrames));
            if (bLightingSweep)
            {
                BeginLightingSweep();
                if (Stage == EStage::LightingSweep) RequestRenderingCapture();
            }
            else
            {
                RequestRenderingCapture();
                if (CaseIndex == 0 && bRenderingCaptureRequested)
                {
                    ProbeFrames = 0;
                    Stage = EStage::RenderingCapture;
                    StageStarted = Now;
                }
                else BeginRuntimeDiagnostics();
            }
        }
    }
}

void UViewerSmokeTestComponent::InspectCurrentSelection()
{
    AViewerPawn* Pawn = Viewer.Get();
    const FViewerOutfit* Outfit = Pawn ? Pawn->GetCurrentOutfit() : nullptr;
    if (!Pawn || !Outfit)
    {
        AddCheck(TEXT("selected_outfit_available"), TEXT("fail"));
        return;
    }
    AddCheck(TEXT("original_role_environment_loaded"), Pawn->Environment ? TEXT("pass") : TEXT("fail"),
        TEXT("Original Scene103 DataAsset required; the temporary cylinder cannot satisfy this check."));
    AddCheck(TEXT("original_role_environment_geometry_loaded"), Pawn->Environment && Pawn->Environment->Meshes.Num() > 0
        && Pawn->StageMeshes.Num() == Pawn->Environment->Meshes.Num() ? TEXT("pass") : TEXT("fail"));
    AddCheck(TEXT("original_role_hdr_skylight_loaded"), Pawn->StageSky && Pawn->StageSky->Cubemap
        && Pawn->StageSky->IsRegistered() ? TEXT("pass") : TEXT("fail"));
    for (const TObjectPtr<UStaticMeshComponent>& SceneMesh : Pawn->StageMeshes)
    {
        const bool Ready = SceneMesh && SceneMesh->IsRegistered() && SceneMesh->GetStaticMesh()
            && SceneMesh->IsVisible() && SceneMesh->IsRenderStateCreated();
        AddCheck(TEXT("environment.") + GetNameSafe(SceneMesh) + TEXT(".registered_render_state"), Ready ? TEXT("pass") : TEXT("fail"));
    }
    AddCheck(TEXT("part_component_count"), Outfit->Parts.Num() > 0 && Pawn->Parts.Num() == Outfit->Parts.Num() ? TEXT("pass") : TEXT("fail"));
    TArray<TSharedPtr<FJsonValue>> PartResults;
    for (int32 Index = 0; Index < Outfit->Parts.Num(); ++Index)
    {
        const FViewerPart& Part = Outfit->Parts[Index];
        const FString Prefix = FString::Printf(TEXT("part[%d]."), Index);
        TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
        Detail->SetStringField(TEXT("id"), Part.Id.ToString());
        Detail->SetStringField(TEXT("mesh_reference"), Part.Mesh.ToSoftObjectPath().ToString());
        USkeletalMeshComponent* Component = Pawn->Parts.IsValidIndex(Index) ? Pawn->Parts[Index].Get() : nullptr;
        const bool MeshMatches = Component && Part.Mesh.Get() && Component->GetSkeletalMeshAsset() == Part.Mesh.Get();
        if (Part.Mesh.Get()) Detail->SetBoolField(TEXT("mesh_supports_ray_tracing"), Part.Mesh.Get()->GetSupportRayTracing());
        AddCheck(Prefix + TEXT("mesh_reference_and_component"), MeshMatches ? TEXT("pass") : TEXT("fail"));
        if (!Component)
        {
            AddCheck(Prefix + TEXT("bounds"), TEXT("unknown"), TEXT("No loaded component to inspect."));
            PartResults.Add(MakeShared<FJsonValueObject>(Detail));
            continue;
        }
        AddCheck(Prefix + TEXT("registered"), Component->IsRegistered() ? TEXT("pass") : TEXT("fail"));
        AddCheck(Prefix + TEXT("render_state_created"), Component->IsRenderStateCreated() ? TEXT("pass") : TEXT("unknown"));
        const auto& Bounds = Component->Bounds;
        const bool BoundsValid = ViewerSmokeTest::FiniteVector(Bounds.Origin) && ViewerSmokeTest::FiniteVector(Bounds.BoxExtent)
            && FMath::IsFinite(Bounds.SphereRadius) && Bounds.SphereRadius > 0.f
            && Bounds.BoxExtent.GetMin() >= 0.f && Bounds.BoxExtent.SizeSquared() > UE_SMALL_NUMBER;
        AddCheck(Prefix + TEXT("finite_nonzero_bounds"), BoundsValid ? TEXT("pass") : TEXT("fail"));
        Detail->SetArrayField(TEXT("bounds_origin"), ViewerSmokeTest::VectorValues(Bounds.Origin));
        Detail->SetArrayField(TEXT("bounds_extent"), ViewerSmokeTest::VectorValues(Bounds.BoxExtent));
        Detail->SetBoolField(TEXT("visible"), Component->IsVisible());
        Detail->SetNumberField(TEXT("bone_count"), Component->GetNumBones());
        if (Component->GetBoneIndex(TEXT("b_Head")) != INDEX_NONE)
            Detail->SetArrayField(TEXT("source_head_world_cm"), ViewerSmokeTest::VectorValues(Component->GetBoneLocation(TEXT("b_Head"))));
        for (const FName Bone : { FName(TEXT("b_Head")), FName(TEXT("b_Hips")) })
        {
            if (Component->GetBoneIndex(Bone) == INDEX_NONE) continue;
            const FVector Position = Component->GetBoneLocation(Bone);
            const bool Contained = ViewerSmokeTest::FiniteVector(Position)
                && Bounds.GetBox().ExpandBy(0.5).IsInsideOrOn(Position);
            AddCheck(Prefix + Bone.ToString() + TEXT(".contained_in_render_bounds"), Contained ? TEXT("pass") : TEXT("fail"),
                TEXT("The actual animated bone must remain within the renderer's bounds, including source root-motion offsets."));
        }
        if (Component->IsVisible())
            AddCheck(Prefix + TEXT("recent_render_observed"), Component->WasRecentlyRendered(1.f) ? TEXT("pass") : TEXT("unknown"));
        const int32 MaterialCount = Component->GetNumMaterials();
        Detail->SetNumberField(TEXT("material_slots"), MaterialCount);
        AddCheck(Prefix + TEXT("has_material_slots"), MaterialCount > 0 ? TEXT("pass") : TEXT("fail"));
        for (int32 Slot = 0; Slot < MaterialCount; ++Slot)
            AddCheck(Prefix + FString::Printf(TEXT("material[%d].nonnull"), Slot), Component->GetMaterial(Slot) ? TEXT("pass") : TEXT("fail"));
        for (int32 Slot = 0; Slot < Part.Materials.Num(); ++Slot)
        {
            const bool Matches = !Part.Materials[Slot].IsNull() && Part.Materials[Slot].Get()
                && Slot < MaterialCount && Component->GetMaterial(Slot) == Part.Materials[Slot].Get();
            AddCheck(Prefix + FString::Printf(TEXT("material[%d].catalog_reference_matches"), Slot), Matches ? TEXT("pass") : TEXT("fail"));
        }
        Detail->SetStringField(TEXT("animation_reference"), Part.Animation.ToSoftObjectPath().ToString());
        if (Part.Animation.IsNull())
            AddCheck(Prefix + TEXT("declared_animation_playing"), TEXT("not_applicable"), TEXT("Catalog declares a reference-pose part without animation."));
        else
        {
            UAnimSingleNodeInstance* Instance = Component->GetSingleNodeInstance();
            const bool Playing = Part.Animation.Get() && Component->GetAnimInstance() && Instance && Instance->IsPlaying()
                && Component->IsPlaying() && Instance->GetCurrentAsset() == Part.Animation.Get();
            AddCheck(Prefix + TEXT("declared_animation_playing"), Playing ? TEXT("pass") : TEXT("fail"));
            const float Position = Component->GetPosition();
            if (FMath::IsFinite(Position)) Detail->SetNumberField(TEXT("animation_position_seconds"), Position);
            else AddCheck(Prefix + TEXT("animation_position_finite"), TEXT("fail"));
        }
        PartResults.Add(MakeShared<FJsonValueObject>(Detail));
    }
    CurrentResult->SetArrayField(TEXT("parts"), PartResults);
    const bool UiVisible = Pawn->ViewerUI && Pawn->ViewerUI->IsInViewport() && Pawn->ViewerUI->IsVisible()
        && Pawn->ViewerUI->GetCachedGeometry().GetLocalSize().GetMin() > 0.f;
    AddCheck(TEXT("viewer_ui_attached_and_laid_out"), UiVisible ? TEXT("pass") : TEXT("unknown"));
}

void UViewerSmokeTestComponent::RequestScreenshot()
{
    ScreenshotPath = FPaths::Combine(RunDirectory, FString::Printf(TEXT("Case_%03d_Character_%02d_Outfit_%02d.png"),
        CaseIndex, Selections[CaseIndex].Character, Selections[CaseIndex].Outfit));
    CurrentResult->SetStringField(TEXT("screenshot"), ScreenshotPath);
    CurrentResult->SetBoolField(TEXT("screenshot_ui_requested"), true);
    bScreenshotProcessed = false;
    LastScreenshotSize = -1;
    ScreenshotSizeStableSince = FPlatformTime::Seconds();
    Stage = EStage::WaitingForScreenshot;
    StageStarted = FPlatformTime::Seconds();
    if (FScreenshotRequest::IsScreenshotRequested())
    {
        AddCheck(TEXT("screenshot_saved"), TEXT("unknown"), TEXT("Another screenshot request was already active; not overwritten."));
        FinishCase();
        return;
    }
    // Keep the standard screenshot writer active; the processed delegate does
    // not intercept the pixel callback or replace its normal disk output.
    FScreenshotRequest::RequestScreenshot(ScreenshotPath, true, false, false, FIntRect(), true);
}

void UViewerSmokeTestComponent::OnScreenshotProcessed()
{
    if (Stage == EStage::WaitingForScreenshot) bScreenshotProcessed = true;
}

bool UViewerSmokeTestComponent::IsScreenshotFileReady()
{
    const double Now = FPlatformTime::Seconds();
    const int64 Size = IFileManager::Get().FileSize(*ScreenshotPath);
    if (Size != LastScreenshotSize)
    {
        LastScreenshotSize = Size;
        ScreenshotSizeStableSince = Now;
    }
    if (Size < 45 || Now - ScreenshotSizeStableSince < 0.2) return false;
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *ScreenshotPath) || Bytes.Num() != Size) return false;
    const uint8 PngSignature[] = {137, 80, 78, 71, 13, 10, 26, 10};
    const uint8 PngEnd[] = {0, 0, 0, 0, 'I', 'E', 'N', 'D', 174, 66, 96, 130};
    return FMemory::Memcmp(Bytes.GetData(), PngSignature, 8) == 0
        && FMemory::Memcmp(Bytes.GetData() + Bytes.Num() - 12, PngEnd, 12) == 0;
}

void UViewerSmokeTestComponent::FinishScreenshot(bool bTimedOut)
{
    if (bTimedOut && FScreenshotRequest::IsScreenshotRequested() && FScreenshotRequest::GetFilename() == ScreenshotPath)
        FScreenshotRequest::Reset();
    TArray<uint8> Bytes;
    const uint8 PngSignature[] = { 137, 80, 78, 71, 13, 10, 26, 10 };
    const bool Loaded = FFileHelper::LoadFileToArray(Bytes, *ScreenshotPath);
    bool Valid = Loaded && Bytes.Num() >= 24 && FMemory::Memcmp(Bytes.GetData(), PngSignature, 8) == 0;
    if (Valid)
    {
        auto ReadBigEndian = [&Bytes](int32 Offset) -> uint32
        {
            return (uint32(Bytes[Offset]) << 24) | (uint32(Bytes[Offset + 1]) << 16)
                | (uint32(Bytes[Offset + 2]) << 8) | uint32(Bytes[Offset + 3]);
        };
        const uint32 Width = ReadBigEndian(16), Height = ReadBigEndian(20);
        Valid = Width > 0 && Height > 0;
        CurrentResult->SetNumberField(TEXT("screenshot_width"), Width);
        CurrentResult->SetNumberField(TEXT("screenshot_height"), Height);
        CurrentResult->SetNumberField(TEXT("screenshot_bytes"), Bytes.Num());
    }
    CurrentResult->SetBoolField(TEXT("screenshot_processed_event_observed"), bScreenshotProcessed);
    CurrentResult->SetNumberField(TEXT("screenshot_file_size_stable_seconds"), FPlatformTime::Seconds() - ScreenshotSizeStableSince);
    CurrentResult->SetBoolField(TEXT("screenshot_complete_iend_and_stable_size_observed"), !bTimedOut && IsScreenshotFileReady());
    AddCheck(TEXT("screenshot_saved"), Valid && !bTimedOut ? TEXT("pass") : TEXT("fail"),
        bTimedOut ? TEXT("Screenshot processing exceeded 20 seconds.") : TEXT("PNG signature and nonzero IHDR dimensions checked on the actual saved image."));
    FinishCase();
}

void UViewerSmokeTestComponent::AddCheck(const FString& Name, const FString& Status, const FString& Detail)
{
    TSharedPtr<FJsonObject> Check = MakeShared<FJsonObject>();
    Check->SetStringField(TEXT("name"), Name);
    Check->SetStringField(TEXT("status"), Status);
    if (!Detail.IsEmpty()) Check->SetStringField(TEXT("detail"), Detail);
    Checks.Add(MakeShared<FJsonValueObject>(Check));
}

FString UViewerSmokeTestComponent::CaseStatus() const
{
    bool Unknown = false;
    for (const TSharedPtr<FJsonValue>& Value : Checks)
    {
        const FString Status = Value->AsObject()->GetStringField(TEXT("status"));
        if (Status == TEXT("fail")) return TEXT("fail");
        Unknown |= Status == TEXT("unknown");
    }
    return Unknown || Checks.IsEmpty() ? TEXT("unknown") : TEXT("pass");
}

void UViewerSmokeTestComponent::FinishCase()
{
    if (CurrentResult)
    {
        if (bLightingSweep) StoreLightingSweepReport();
        RestoreLightingGridOverride();
        if (Viewer.IsValid())
        {
            Viewer->ResetView();
            RestoreUserSettings(true);
        }
        CurrentResult->SetArrayField(TEXT("checks"), Checks);
        CurrentResult->SetStringField(TEXT("status"), CaseStatus());
        CurrentResult->SetStringField(TEXT("finished_utc"), FDateTime::UtcNow().ToIso8601());
        Results.Add(MakeShared<FJsonValueObject>(CurrentResult));
        CurrentResult.Reset();
    }
    Checks.Reset();
    Stage = EStage::Advance;
    StageStarted = FPlatformTime::Seconds();
    WriteReport(TEXT("running"));
}

void UViewerSmokeTestComponent::WriteReport(const FString& Status)
{
    if (RunDirectory.IsEmpty()) return;
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"), Status);
    Report->SetStringField(TEXT("started_utc"), StartedUtc);
    Report->SetStringField(TEXT("updated_utc"), FDateTime::UtcNow().ToIso8601());
    Report->SetStringField(TEXT("run_directory"), RunDirectory);
    Report->SetStringField(TEXT("catalog"), Viewer.IsValid() && Viewer->Catalog ? Viewer->Catalog->GetPathName() : TEXT(""));
    Report->SetNumberField(TEXT("planned_combinations"), Selections.Num());
    Report->SetNumberField(TEXT("completed_combinations"), Results.Num());
    Report->SetArrayField(TEXT("results"), Results);
    if (CurrentResult)
    {
        CurrentResult->SetArrayField(TEXT("checks"), Checks);
        Report->SetObjectField(TEXT("current_case"), CurrentResult);
    }
    Report->SetBoolField(TEXT("force_gc_used"), false);
    Report->SetBoolField(TEXT("all_characters_preloaded"), false);
    Report->SetBoolField(TEXT("validation_settings_persisted"), false);
    Report->SetBoolField(TEXT("original_user_runtime_settings_restored"), bPreferencesRestored);
    Report->SetBoolField(TEXT("lighting_sweep_mode"), bLightingSweep);
    Report->SetBoolField(TEXT("lighting_grid_override_restored"), bLightingGridRestored);
    Report->SetBoolField(TEXT("visual_review_of_screenshots_still_required"), true);
    Report->SetStringField(TEXT("validated_scope"), bLightingSweep
        ? TEXT("Development lighting-sweep acquisition only: actual per-frame turn/camera, projected landmark, viewport, frame delta and source-light observations; periodic screenshot requests and saved PNG presence. A completed acquisition does not establish stable lighting or absence of flicker; matched screenshot sequences still require image analysis. Temporary settings are not persisted.")
        : TEXT("Runtime Catalog selection and sequential async loading; actual references/bounds/playback; supported FPS and screen-percentage CVar/Preferences roundtrips without persistence; actively driven bounded secondary-motion diagnostics, disable/base-pose and replay/switch resets; actual viewport/UI geometry and saved game screenshots. No original-solver, RHI internal-resolution, physical-fold event, shader-appearance or iOS device result is inferred."));
    FString Json;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
    const bool Serialized = FJsonSerializer::Serialize(Report, Writer);
    const bool RunSaved = Serialized && FFileHelper::SaveStringToFile(Json, *FPaths::Combine(RunDirectory, TEXT("Report.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    const bool LatestSaved = Serialized && FFileHelper::SaveStringToFile(Json, *FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("ViewerSmokeTest/LatestReport.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    bReportWriteFailed |= !RunSaved || !LatestSaved;
}

void UViewerSmokeTestComponent::Complete()
{
    RestoreLightingGridOverride();
    RestoreUserSettings(false);
    RestoreRenderingCaptureSettings();
    if (Viewer.IsValid() && Viewer->ViewerUI) Viewer->ViewerUI->SetIsEnabled(bSavedUiEnabled);
    Stage = EStage::Complete;
    SetComponentTickEnabled(false);
    FScreenshotRequest::OnScreenshotRequestProcessed().Remove(ScreenshotDelegate);
    bool Failed = bReportWriteFailed || !bLightingGridRestored || Results.IsEmpty();
    bool Unknown = bUnexpectedInterruption || Results.Num() != Selections.Num();
    for (const TSharedPtr<FJsonValue>& Result : Results)
    {
        const FString Status = Result->AsObject()->GetStringField(TEXT("status"));
        Failed |= Status == TEXT("fail");
        Unknown |= Status == TEXT("unknown");
    }
    FString Status = Failed ? TEXT("fail") : Unknown ? TEXT("unknown") : TEXT("pass");
    WriteReport(Status);
    if (bReportWriteFailed && Status != TEXT("fail"))
    {
        Status = TEXT("fail");
        WriteReport(Status);
    }
    UE_LOG(LogTemp, Display, TEXT("ViewerSmokeTest finished: %s; %d/%d combinations; %s"),
        *Status, Results.Num(), Selections.Num(), *FPaths::Combine(RunDirectory, TEXT("Report.json")));
    // Never close an Editor/PIE session. Only the explicit standalone -game
    // process may request a clean main-loop exit after all results are saved.
    if (IsRunningGame() && FParse::Param(FCommandLine::Get(), TEXT("game"))
        && GetWorld() && GetWorld()->WorldType == EWorldType::Game)
        FPlatformMisc::RequestExitWithStatus(false, Failed || bReportWriteFailed ? 1 : Unknown ? 2 : 0, TEXT("ViewerSmokeTest completed"));
}

void UViewerSmokeTestComponent::Stop()
{
    if (!IsRunning()) return;
    bUnexpectedInterruption = true;
    if (bLightingSweep && FScreenshotRequest::IsScreenshotRequested()
        && FScreenshotRequest::GetFilename() == LastLightingScreenshotPath)
        FScreenshotRequest::Reset();
    if (Stage == EStage::WaitingForScreenshot && FScreenshotRequest::IsScreenshotRequested()
        && FScreenshotRequest::GetFilename() == ScreenshotPath)
        FScreenshotRequest::Reset();
    if (CurrentResult)
    {
        AddCheck(TEXT("case_completed_before_world_end"), TEXT("unknown"), TEXT("World or pawn ended before the case completed."));
        FinishCase();
    }
    RestoreLightingGridOverride();
    RestoreUserSettings(false);
    RestoreRenderingCaptureSettings();
    if (Viewer.IsValid() && Viewer->ViewerUI) Viewer->ViewerUI->SetIsEnabled(bSavedUiEnabled);
    Stage = EStage::Complete;
    SetComponentTickEnabled(false);
    FScreenshotRequest::OnScreenshotRequestProcessed().Remove(ScreenshotDelegate);
    WriteReport(TEXT("unknown"));
}

void UViewerSmokeTestComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    Stop();
    Super::EndPlay(EndPlayReason);
}

void UViewerSmokeTestComponent::SaveUserSettings()
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn || !Pawn->Preferences) return;
    SavedPreferences = DuplicateObject<UViewerPreferences>(Pawn->Preferences, this);
    SavedEngineMaxFps = GEngine ? GEngine->GetMaxFPS() : 0.f;
    SavedFramePace = FPlatformRHIFramePacer::GetFramePace();
    for (const TCHAR* Name : {TEXT("t.MaxFPS"), TEXT("r.ScreenPercentage"), TEXT("rhi.SyncInterval"),
        TEXT("sg.ViewDistanceQuality"), TEXT("sg.AntiAliasingQuality"), TEXT("sg.ShadowQuality"),
        TEXT("sg.GlobalIlluminationQuality"), TEXT("sg.ReflectionQuality"), TEXT("sg.PostProcessQuality"),
        TEXT("sg.TextureQuality"), TEXT("sg.EffectsQuality"), TEXT("sg.FoliageQuality"), TEXT("sg.ShadingQuality"),
        TEXT("sg.ResolutionQuality"), TEXT("r.MSAACount"), TEXT("r.ShadowQuality"), TEXT("r.Shadow.MaxResolution"),
        TEXT("r.Shadow.CSM.MaxCascades"), TEXT("r.MaxAnisotropy"), TEXT("r.Streaming.MipBias"),
        TEXT("r.BloomQuality"), TEXT("r.DepthOfFieldQuality"), TEXT("r.Tonemapper.Sharpen"), TEXT("r.Streaming.PoolSize"),
        TEXT("r.Shadow.MaxCSMResolution"), TEXT("r.Shadow.MaxNumPointShadowCacheUpdatesPerFrame"),
        TEXT("r.Mobile.AntiAliasing"), TEXT("r.AntiAliasingMethod"), TEXT("r.Tonemapper.Quality"),
        TEXT("r.Mobile.AmbientOcclusionQuality"), TEXT("r.AmbientOcclusionLevels"), TEXT("r.AmbientOcclusionMaxQuality"),
        TEXT("r.SSR.Quality"), TEXT("r.SSR.HalfResSceneColor")})
        if (IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(Name))
            SavedConsoleVariables.Add(Name, Variable->GetString());
}

void UViewerSmokeTestComponent::RestoreUserSettings(bool bRecord)
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn || !Pawn->Preferences || !SavedPreferences)
    {
        if (bRecord) AddCheck(TEXT("user_settings_restored_without_persist"), TEXT("unknown"), TEXT("No original runtime preference snapshot was available."));
        return;
    }
    UViewerPreferences* Preferences = Pawn->Preferences;
    Preferences->FrameRate = SavedPreferences->FrameRate;
    Preferences->Quality = SavedPreferences->Quality;
    Preferences->ResolutionPercent = SavedPreferences->ResolutionPercent;
    Preferences->Textures = SavedPreferences->Textures;
    Preferences->Shadows = SavedPreferences->Shadows;
    Preferences->Antialiasing = SavedPreferences->Antialiasing;
    Preferences->bPostProcessing = SavedPreferences->bPostProcessing;
    Preferences->bBloom = SavedPreferences->bBloom;
    Preferences->bVignette = SavedPreferences->bVignette;
    Preferences->bDepthOfField = SavedPreferences->bDepthOfField;
    Preferences->bSecondaryMotion = SavedPreferences->bSecondaryMotion;
    Preferences->bAmbientOcclusion = SavedPreferences->bAmbientOcclusion;
    Preferences->bScreenSpaceReflections = SavedPreferences->bScreenSpaceReflections;
    Preferences->GraphicsVersion = SavedPreferences->GraphicsVersion;
    Pawn->ApplyPreferences(); // Never uses the public setters that Persist.
    if (GEngine) GEngine->SetMaxFPS(SavedEngineMaxFps);
    FPlatformRHIFramePacer::SetFramePace(SavedFramePace);
    bool Restored = Preferences->FrameRate == SavedPreferences->FrameRate
        && Preferences->Quality == SavedPreferences->Quality
        && Preferences->ResolutionPercent == SavedPreferences->ResolutionPercent
        && Preferences->Textures == SavedPreferences->Textures && Preferences->Shadows == SavedPreferences->Shadows
        && Preferences->Antialiasing == SavedPreferences->Antialiasing
        && Preferences->bPostProcessing == SavedPreferences->bPostProcessing
        && Preferences->bBloom == SavedPreferences->bBloom && Preferences->bVignette == SavedPreferences->bVignette
        && Preferences->bDepthOfField == SavedPreferences->bDepthOfField
        && Preferences->bSecondaryMotion == SavedPreferences->bSecondaryMotion
        && Preferences->bAmbientOcclusion == SavedPreferences->bAmbientOcclusion
        && Preferences->bScreenSpaceReflections == SavedPreferences->bScreenSpaceReflections
        && Preferences->GraphicsVersion == SavedPreferences->GraphicsVersion;
    for (const TPair<FString, FString>& Pair : SavedConsoleVariables)
        if (IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(*Pair.Key))
        {
            Variable->SetWithCurrentPriority(*Pair.Value);
            const double Expected = FCString::Atod(*Pair.Value), Actual = Variable->GetFloat();
            Restored &= FMath::IsFinite(Actual) && FMath::IsNearlyEqual(Expected, Actual, 0.001);
        }
        else Restored = false;
    Restored &= RestoreRenderingCaptureSettings();
    if (bRecord && bRenderingCaptureRequested)
        AddCheck(TEXT("gpu_profile_showui_value_and_priority_restored"), bProfileShowUIRestored ? TEXT("pass") : TEXT("fail"),
            TEXT("The diagnostic's tagged temporary ShowUI override is removed; both original value and SetBy priority are checked."));
    bPreferencesRestored = Restored;
    if (bRecord) AddCheck(TEXT("user_settings_restored_without_persist"), Restored ? TEXT("pass") : TEXT("fail"),
        TEXT("Original in-memory preference fields, engine FPS cap, frame pacing and captured runtime CVars restored; no SaveGame write."));
    if (Pawn->ViewerUI) Pawn->ViewerUI->Refresh();
}

void UViewerSmokeTestComponent::RequestRenderingCapture()
{
    if (CaseIndex != 0 || bRenderingCaptureRequested ||
        !FParse::Param(FCommandLine::Get(), TEXT("ViewerRenderingCapture"))) return;
    bRenderingCaptureRequested = true;
    AViewerPawn* Pawn = Viewer.Get();
    IConsoleVariable* ShowUI = IConsoleManager::Get().FindConsoleVariable(TEXT("r.ProfileGPU.ShowUI"));
    const bool Available = Pawn && GEngine && ShowUI;
    bool Accepted = false;
    if (Available)
    {
        SavedProfileShowUIValue = ShowUI->GetString();
        SavedProfileShowUIPriority = static_cast<uint32>(ShowUI->GetFlags() & ECVF_SetByMask);
        // Constructor defaults cannot be implicitly replaced in UE 5.8.
        // A tagged temporary history entry preserves the original priority
        // and can be removed without leaving a high-priority code override.
        ShowUI->Set(TEXT("0"), ECVF_SetByTemp, FName(TEXT("ViewerRenderingCapture")));
        bProfileShowUIOverridden = true;
        bProfileShowUIRestored = false;
        if (ShowUI->GetInt() == 0) Accepted = GEngine->Exec(Pawn->GetWorld(), TEXT("ProfileGPU"));
    }
    if (CurrentResult)
    {
        CurrentResult->SetBoolField(TEXT("requested_gpu_profile_capture"), true);
        CurrentResult->SetBoolField(TEXT("gpu_profile_command_accepted"), Accepted);
        CurrentResult->SetBoolField(TEXT("gpu_profile_showui_disabled"), ShowUI && ShowUI->GetInt() == 0);
        CurrentResult->SetStringField(TEXT("gpu_profile_scope"),
            TEXT("One ProfileGPU command after the first selection settles, with ShowUI disabled. The command result does not prove GPU events were captured; inspect the process log for the GPU profile. No SaveGame write."));
    }
    AddCheck(TEXT("gpu_profile_capture_command"), Accepted ? TEXT("pass") : TEXT("unknown"),
        TEXT("Optional diagnostic command only. Verify the resulting GPU events in the process log; command acceptance is not pass-execution evidence."));
}

bool UViewerSmokeTestComponent::RestoreRenderingCaptureSettings()
{
    if (!bProfileShowUIOverridden) return bProfileShowUIRestored;
    IConsoleVariable* ShowUI = IConsoleManager::Get().FindConsoleVariable(TEXT("r.ProfileGPU.ShowUI"));
    if (ShowUI)
    {
        ShowUI->Unset(ECVF_SetByTemp, FName(TEXT("ViewerRenderingCapture")));
        bProfileShowUIRestored = ShowUI->GetString() == SavedProfileShowUIValue
            && static_cast<uint32>(ShowUI->GetFlags() & ECVF_SetByMask) == SavedProfileShowUIPriority;
    }
    else bProfileShowUIRestored = false;
    bProfileShowUIOverridden = false;
    return bProfileShowUIRestored;
}

void UViewerSmokeTestComponent::BeginLightingSweep()
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn || !Pawn->Preferences || !SavedPreferences)
    {
        AddCheck(TEXT("lighting_sweep_preferences_available"), TEXT("fail"));
        FinishCase();
        return;
    }
    bLightingSweepStarted = true;
    LightingSweepReport = MakeShared<FJsonObject>();
    LightingSweepSamples.Reset();
    LightingSweepScreenshots.Reset();
    LightingScreenshotIndex = LightingScreenshotBusySkips = 0;
    NextLightingScreenshotSeconds = 0.0;
    LastLightingScreenshotPath.Reset();
    const bool NoTAA = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingNoTAA"));
    const bool ForceTAA = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingTAA"));
    const bool ForceSSR = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingSSR"));
    int32 RequestedMSAA = 0;
    const bool HasMSAA = FParse::Value(FCommandLine::Get(), TEXT("ViewerLightingMSAA="), RequestedMSAA);
    const bool NoSSR = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingNoSSR")) || NoTAA || HasMSAA;
    const bool NoShadows = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingNoShadows"));
    const bool NoAO = FParse::Param(FCommandLine::Get(), TEXT("ViewerLightingNoAO"));
    LightingSweepReport->SetBoolField(TEXT("requested_no_ssr"), NoSSR);
    LightingSweepReport->SetBoolField(TEXT("requested_force_ssr"), ForceSSR);
    LightingSweepReport->SetBoolField(TEXT("requested_no_shadows"), NoShadows);
    LightingSweepReport->SetBoolField(TEXT("requested_no_ao"), NoAO);
    LightingSweepReport->SetBoolField(TEXT("requested_no_taa"), NoTAA);
    LightingSweepReport->SetBoolField(TEXT("requested_force_taa"), ForceTAA);
    if (HasMSAA) LightingSweepReport->SetNumberField(TEXT("requested_msaa_samples"), RequestedMSAA);
    const bool Legacy = FParse::Param(FCommandLine::Get(), TEXT("ViewerLegacyTurntable"));
    LightingSweepReport->SetStringField(TEXT("turn_mode"), Legacy ? TEXT("legacy_scene_turntable") : TEXT("camera_orbit"));
    LightingSweepReport->SetNumberField(TEXT("initial_still_seconds"), ViewerSmokeTest::LightingStillSeconds);
    LightingSweepReport->SetNumberField(TEXT("motion_cycle_seconds"), ViewerSmokeTest::LightingCycleSeconds);
    LightingSweepReport->SetNumberField(TEXT("motion_cycles"), 2);
    LightingSweepReport->SetNumberField(TEXT("final_still_seconds"), ViewerSmokeTest::LightingStopSeconds);
    LightingSweepReport->SetNumberField(TEXT("planned_duration_seconds"), ViewerSmokeTest::LightingTotalSeconds);
    LightingSweepReport->SetNumberField(TEXT("requested_capture_interval_seconds"), ViewerSmokeTest::LightingCaptureInterval);
    LightingSweepReport->SetStringField(TEXT("scope"), TEXT("Per-frame observations and periodic screenshot acquisition only. Screenshots are requested after this component's observation, before the viewport renders; exact GPU execution timing is not inferred. PNG presence does not establish absence of flicker. Screenshot I/O can affect frame time."));
    if (ForceSSR && NoSSR)
    {
        AddCheck(TEXT("lighting_sweep_ssr_condition_unambiguous"), TEXT("fail"),
            TEXT("ViewerLightingSSR requires TAA; do not combine it with NoSSR, NoTAA or MSAA."));
        FinishCase();
        return;
    }
    // Apply these fields directly: public Pawn preference setters persist.
    if (NoSSR) Pawn->Preferences->bScreenSpaceReflections = false;
    if (ForceSSR)
    {
        Pawn->Preferences->bPostProcessing = true;
        Pawn->Preferences->bScreenSpaceReflections = true;
        Pawn->Preferences->Antialiasing = 0;
    }
    if (NoShadows) Pawn->Preferences->Shadows = 0;
    if (NoAO) Pawn->Preferences->bAmbientOcclusion = false;
    if (NoTAA) Pawn->Preferences->Antialiasing = 1; // No AA; SSR is explicitly disabled above.
    else if (HasMSAA)
    {
        const bool Supported = RequestedMSAA > 1 && UViewerPreferences::AntialiasingSamples().Contains(RequestedMSAA);
        AddCheck(TEXT("lighting_sweep_requested_msaa_supported"), Supported ? TEXT("pass") : TEXT("fail"),
            TEXT("Both the current renderer and native Metal sample-count query must allow this MSAA count."));
        if (Supported) Pawn->Preferences->Antialiasing = RequestedMSAA;
    }
    else if (ForceTAA) Pawn->Preferences->Antialiasing = 0;
    if (static_cast<int32>(NoTAA) + static_cast<int32>(ForceTAA) + static_cast<int32>(HasMSAA) > 1)
        AddCheck(TEXT("lighting_sweep_aa_condition_unambiguous"), TEXT("fail"), TEXT("Specify at most one of NoTAA, TAA or MSAA."));
    Pawn->ApplyPreferences();
    bPreferencesRestored = false;
    LightingSweepReport->SetNumberField(TEXT("applied_antialiasing_samples"), Pawn->Preferences->Antialiasing);
    LightingSweepReport->SetBoolField(TEXT("applied_ssr_preference"), Pawn->Preferences->bScreenSpaceReflections);
    LightingSweepReport->SetBoolField(TEXT("applied_postprocessing_preference"), Pawn->Preferences->bPostProcessing);
    LightingSweepReport->SetBoolField(TEXT("applied_ao_preference"), Pawn->Preferences->bAmbientOcclusion);
    LightingSweepReport->SetNumberField(TEXT("applied_shadow_preference"), Pawn->Preferences->Shadows);
    int32 RequestedGrid = 0;
    if (FParse::Value(FCommandLine::Get(), TEXT("ViewerLightingGrid="), RequestedGrid))
    {
        LightingSweepReport->SetNumberField(TEXT("requested_max_lights_per_cell"), RequestedGrid);
        IConsoleVariable* Grid = IConsoleManager::Get().FindConsoleVariable(TEXT("r.Forward.MaxCulledLightsPerCell"));
        if (!Grid || RequestedGrid < 1 || RequestedGrid > 256)
            AddCheck(TEXT("lighting_sweep_grid_override_applied"), TEXT("fail"), TEXT("Expected an available grid CVar and a value in 1..256."));
        else
        {
            SavedLightingGridValue = Grid->GetString();
            bLightingGridOverridden = true;
            bLightingGridRestored = false;
            Grid->SetWithCurrentPriority(*FString::FromInt(RequestedGrid));
            AddCheck(TEXT("lighting_sweep_grid_override_applied"), Grid->GetInt() == RequestedGrid ? TEXT("pass") : TEXT("fail"));
        }
    }
    TSharedPtr<FJsonObject> Variables = MakeShared<FJsonObject>();
    for (const TCHAR* Name : { TEXT("r.Forward.MaxCulledLightsPerCell"), TEXT("r.Mobile.AntiAliasing"),
        TEXT("r.AntiAliasingMethod"), TEXT("r.MSAACount"), TEXT("r.Mobile.ScreenSpaceReflections"),
        TEXT("r.DynamicGlobalIlluminationMethod"), TEXT("r.Lumen.DiffuseIndirect.Allow"),
        TEXT("r.SSR.Quality"), TEXT("r.SSR.HalfResSceneColor"), TEXT("r.ShadowQuality"),
        TEXT("r.Mobile.AmbientOcclusionQuality"), TEXT("r.AmbientOcclusionLevels"), TEXT("r.Tonemapper.Sharpen") })
        if (IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(Name))
            Variables->SetStringField(Name, Variable->GetString());
    LightingSweepReport->SetObjectField(TEXT("applied_console_variables"), Variables);
    if (Pawn->StagePostProcess)
    {
        const FPostProcessSettings& Post = Pawn->StagePostProcess->Settings;
        LightingSweepReport->SetNumberField(TEXT("applied_stage_global_illumination_method"), static_cast<int32>(Post.DynamicGlobalIlluminationMethod.GetValue()));
        LightingSweepReport->SetNumberField(TEXT("applied_stage_reflection_method"), static_cast<int32>(Post.ReflectionMethod.GetValue()));
        LightingSweepReport->SetNumberField(TEXT("applied_stage_ssr_intensity"), Post.ScreenSpaceReflectionIntensity);
        LightingSweepReport->SetNumberField(TEXT("applied_stage_ssr_quality"), Post.ScreenSpaceReflectionQuality);
        LightingSweepReport->SetNumberField(TEXT("applied_stage_ssr_max_roughness"), Post.ScreenSpaceReflectionMaxRoughness);
        LightingSweepReport->SetStringField(TEXT("ssr_observation_scope"),
            TEXT("Runtime CVars and stage settings only; these do not prove a reflection ray hit or a visible SSR contribution. Compare matched screenshots with SSR disabled."));
        if (ForceSSR)
        {
            const IConsoleVariable* SSRQuality = IConsoleManager::Get().FindConsoleVariable(TEXT("r.SSR.Quality"));
            const IConsoleVariable* MobileAA = IConsoleManager::Get().FindConsoleVariable(TEXT("r.Mobile.AntiAliasing"));
            const IConsoleVariable* Samples = IConsoleManager::Get().FindConsoleVariable(TEXT("r.MSAACount"));
            const bool Enabled = Pawn->Preferences->bPostProcessing && Pawn->Preferences->bScreenSpaceReflections
                && Pawn->Preferences->Antialiasing == 0 && SSRQuality && SSRQuality->GetInt() > 0
                && Post.bOverride_ScreenSpaceReflectionIntensity && Post.ScreenSpaceReflectionIntensity > 0.f
                && (!UViewerPreferences::UsesMobileRenderer() ||
                    (AreMobileScreenSpaceReflectionsEnabled(GMaxRHIShaderPlatform)
                        && MobileAA && MobileAA->GetInt() == 2 && Samples && Samples->GetInt() == 1
                        && Post.bOverride_ReflectionMethod && Post.ReflectionMethod == EReflectionMethod::ScreenSpace));
            AddCheck(TEXT("lighting_sweep_requested_ssr_settings_enabled"), Enabled ? TEXT("pass") : TEXT("fail"),
                TEXT("Checks runtime preference, TAA, SSR CVar, nonzero intensity, and the mobile shader capability/reflection method. Visible reflection contribution needs image comparison."));
        }
    }
    else if (ForceSSR) AddCheck(TEXT("lighting_sweep_requested_ssr_settings_enabled"), TEXT("fail"), TEXT("Stage postprocess settings unavailable."));
    Pawn->ResetView();
    float RequestedZoom = 1.f;
    if (FParse::Value(FCommandLine::Get(), TEXT("ViewerLightingZoom="), RequestedZoom))
    {
        const bool Valid = FMath::IsFinite(RequestedZoom) && RequestedZoom >= 0.25f && RequestedZoom <= 4.f
            && FMath::IsFinite(Pawn->Distance) && Pawn->Distance > 0.f;
        AddCheck(TEXT("lighting_sweep_requested_zoom_valid"), Valid ? TEXT("pass") : TEXT("fail"),
            TEXT("ViewerLightingZoom must be finite and between 0.25 and 4 inclusive."));
        if (!Valid)
        {
            LightingSweepReport->SetStringField(TEXT("requested_zoom_status"), TEXT("invalid_outside_finite_0.25_to_4_range"));
            FinishCase();
            return;
        }
        const float ResetDistance = Pawn->Distance;
        LightingSweepReport->SetNumberField(TEXT("requested_zoom_factor"), RequestedZoom);
        LightingSweepReport->SetNumberField(TEXT("reset_distance_parameter_cm"), ResetDistance);
        LightingSweepReport->SetNumberField(TEXT("requested_distance_parameter_cm"), ResetDistance / RequestedZoom);
        Pawn->Distance = ResetDistance / RequestedZoom;
        Pawn->UpdateCamera();
        LightingSweepReport->SetNumberField(TEXT("applied_distance_parameter_cm"), Pawn->Distance);
        if (Pawn->Camera)
            LightingSweepReport->SetNumberField(TEXT("applied_camera_horizontal_fov_degrees"), Pawn->Camera->FieldOfView);
        LightingSweepReport->SetStringField(TEXT("zoom_scope"),
            TEXT("Uses the viewer's Distance zoom parameter after ResetView. The production camera converts it to field of view; it does not move the camera through stage geometry. Sweep timing and orbit are unchanged."));
    }
    Pawn->ReplayAnimation();
    Stage = EStage::LightingSweep;
    StageStarted = FPlatformTime::Seconds();
    CurrentResult->SetObjectField(TEXT("lighting_sweep"), LightingSweepReport);
    WriteReport(TEXT("running"));
}

void UViewerSmokeTestComponent::TickLightingSweep(float DeltaTime)
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn || !LightingSweepReport) { Stop(); return; }
    const double Elapsed = FPlatformTime::Seconds() - StageStarted;
    const double MotionTime = Elapsed - ViewerSmokeTest::LightingStillSeconds;
    const bool Moving = MotionTime >= 0.0 && MotionTime < ViewerSmokeTest::LightingMotionSeconds;
    const float NextYaw = Moving ? 45.f * FMath::Sin(static_cast<float>(MotionTime / ViewerSmokeTest::LightingCycleSeconds * 2.0 * PI)) : 0.f;
    TSharedPtr<FJsonObject> Frame = MakeShared<FJsonObject>();
    Frame->SetNumberField(TEXT("elapsed_seconds"), Elapsed);
    Frame->SetNumberField(TEXT("delta_seconds"), DeltaTime);
    Frame->SetStringField(TEXT("engine_frame"), LexToString(GFrameCounter));
    Frame->SetStringField(TEXT("phase"), Elapsed < ViewerSmokeTest::LightingStillSeconds ? TEXT("initial_still")
        : Moving ? TEXT("sweep") : Elapsed < ViewerSmokeTest::LightingTotalSeconds ? TEXT("final_still") : TEXT("capture_drain"));
    // Pawn Tick has already applied the previous target. Keep the observed yaw
    // and next target separate so projected landmarks match the sampled camera.
    Frame->SetNumberField(TEXT("requested_yaw"), Pawn->Yaw);
    Frame->SetNumberField(TEXT("display_yaw"), FMath::UnwindDegrees(Pawn->DisplayYaw));
    Frame->SetNumberField(TEXT("next_requested_yaw"), NextYaw);
    APlayerController* PC = Cast<APlayerController>(Pawn->GetController());
    int32 Width = 0, Height = 0;
    if (PC) PC->GetViewportSize(Width, Height);
    Frame->SetArrayField(TEXT("viewport_pixel_rect"), { MakeShared<FJsonValueNumber>(0), MakeShared<FJsonValueNumber>(0),
        MakeShared<FJsonValueNumber>(Width), MakeShared<FJsonValueNumber>(Height) });
    if (Pawn->Camera)
    {
        Frame->SetNumberField(TEXT("camera_horizontal_fov_degrees"), Pawn->Camera->FieldOfView);
        Frame->SetArrayField(TEXT("camera_world_position_cm"), ViewerSmokeTest::VectorValues(Pawn->Camera->GetComponentLocation()));
        const FRotator Rotation = Pawn->Camera->GetComponentRotation();
        Frame->SetArrayField(TEXT("camera_rotation_pitch_yaw_roll"), ViewerSmokeTest::VectorValues(FVector(Rotation.Pitch, Rotation.Yaw, Rotation.Roll)));
    }
    bool LandmarkFound = false;
    for (const FName Bone : { FName(TEXT("b_Head")), FName(TEXT("b_Chest")), FName(TEXT("b_Spine2")), FName(TEXT("b_Hips")) })
    {
        for (USkeletalMeshComponent* Part : Pawn->Parts)
        {
            if (!Part || !Part->IsVisible() || Part->GetBoneIndex(Bone) == INDEX_NONE) continue;
            const FVector WorldPosition = Part->GetBoneLocation(Bone);
            FVector2D Pixel;
            const bool Projected = PC && PC->ProjectWorldLocationToScreen(WorldPosition, Pixel, false)
                && FMath::IsFinite(Pixel.X) && FMath::IsFinite(Pixel.Y);
            Frame->SetStringField(TEXT("landmark_bone"), Bone.ToString());
            Frame->SetArrayField(TEXT("landmark_world_position_cm"), ViewerSmokeTest::VectorValues(WorldPosition));
            Frame->SetBoolField(TEXT("landmark_projected"), Projected);
            if (Projected)
                Frame->SetArrayField(TEXT("landmark_screen_pixels"), { MakeShared<FJsonValueNumber>(Pixel.X), MakeShared<FJsonValueNumber>(Pixel.Y) });
            LandmarkFound = true;
            break;
        }
        if (LandmarkFound) break;
    }
    Frame->SetBoolField(TEXT("landmark_available"), LandmarkFound);
    TArray<TSharedPtr<FJsonValue>> Lights;
    double IntensitySum = 0.0;
    for (const ULightComponent* Light : Pawn->StageLights)
    {
        if (!Light) continue;
        TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
        Detail->SetStringField(TEXT("component"), Light->GetName());
        Detail->SetNumberField(TEXT("intensity"), Light->Intensity);
        Detail->SetBoolField(TEXT("visible"), Light->IsVisible());
        Detail->SetBoolField(TEXT("casts_shadows"), Light->CastShadows);
        Detail->SetArrayField(TEXT("world_position_cm"), ViewerSmokeTest::VectorValues(Light->GetComponentLocation()));
        Detail->SetArrayField(TEXT("world_direction"), ViewerSmokeTest::VectorValues(Light->GetForwardVector()));
        Lights.Add(MakeShared<FJsonValueObject>(Detail));
        IntensitySum += Light->Intensity;
    }
    Frame->SetNumberField(TEXT("local_and_directional_light_intensity_sum"), IntensitySum);
    Frame->SetArrayField(TEXT("source_lights"), Lights);
    if (Pawn->StageSky)
    {
        Frame->SetNumberField(TEXT("sky_intensity"), Pawn->StageSky->Intensity);
        Frame->SetNumberField(TEXT("sky_source_cubemap_angle"), Pawn->StageSky->SourceCubemapAngle);
        Frame->SetArrayField(TEXT("sky_world_position_cm"), ViewerSmokeTest::VectorValues(Pawn->StageSky->GetComponentLocation()));
    }
    if (Elapsed < ViewerSmokeTest::LightingTotalSeconds && Elapsed >= NextLightingScreenshotSeconds)
    {
        NextLightingScreenshotSeconds = Elapsed + ViewerSmokeTest::LightingCaptureInterval;
        if (!FScreenshotRequest::IsScreenshotRequested())
        {
            LastLightingScreenshotPath = FPaths::Combine(RunDirectory, FString::Printf(TEXT("Sweep_%03d.png"), LightingScreenshotIndex++));
            LightingSweepScreenshots.Add(LastLightingScreenshotPath);
            Frame->SetStringField(TEXT("screenshot_requested"), LastLightingScreenshotPath);
            // Reuse the PNG readiness helper only for the final acquisition drain.
            // OnScreenshotProcessed deliberately ignores the LightingSweep stage.
            ScreenshotPath = LastLightingScreenshotPath;
            LastScreenshotSize = -1;
            ScreenshotSizeStableSince = FPlatformTime::Seconds();
            // Capture the game buffer directly. Slate's OS-window capture can
            // fail during live resizing and adds UI pixels to lighting samples.
            FScreenshotRequest::RequestScreenshot(LastLightingScreenshotPath, false, false, false, FIntRect(), true);
        }
        else ++LightingScreenshotBusySkips;
    }
    LightingSweepSamples.Add(MakeShared<FJsonValueObject>(Frame));
    Pawn->Yaw = NextYaw;
    if (Elapsed < ViewerSmokeTest::LightingTotalSeconds) return;
    const bool OwnRequestPending = FScreenshotRequest::IsScreenshotRequested()
        && FScreenshotRequest::GetFilename() == LastLightingScreenshotPath;
    const bool LastPngReady = !LastLightingScreenshotPath.IsEmpty() && IsScreenshotFileReady();
    if ((OwnRequestPending || !LastPngReady) && Elapsed < ViewerSmokeTest::LightingTotalSeconds + 3.0) return;
    if (OwnRequestPending) FScreenshotRequest::Reset();
    float MinimumYaw = 0.f, MaximumYaw = 0.f;
    for (const TSharedPtr<FJsonValue>& Sample : LightingSweepSamples)
    {
        const float Value = Sample->AsObject()->GetNumberField(TEXT("display_yaw"));
        MinimumYaw = FMath::Min(MinimumYaw, Value);
        MaximumYaw = FMath::Max(MaximumYaw, Value);
    }
    AddCheck(TEXT("lighting_sweep_acquisition_completed"), LightingSweepSamples.Num() >= 20 && MinimumYaw < -20.f && MaximumYaw > 20.f
        ? TEXT("pass") : TEXT("unknown"), TEXT("Requested two six-second +/-45 degree turns with initial/final still phases; checks observed turn samples, not lighting stability."));
    AddCheck(TEXT("lighting_sweep_screenshot_requests_issued"), LightingSweepScreenshots.IsEmpty() ? TEXT("unknown") : TEXT("pass"));
    InspectCurrentSelection();
    InspectViewportAndUi();
    FinishCase();
}

void UViewerSmokeTestComponent::StoreLightingSweepReport()
{
    if (!CurrentResult || !bLightingSweepStarted || !LightingSweepReport) return;
    LightingSweepReport->SetArrayField(TEXT("frames"), LightingSweepSamples);
    LightingSweepReport->SetNumberField(TEXT("recorded_frames"), LightingSweepSamples.Num());
    LightingSweepReport->SetNumberField(TEXT("screenshot_requests_skipped_busy"), LightingScreenshotBusySkips);
    LightingSweepReport->SetBoolField(TEXT("flicker_stability_assessed"), false);
    TArray<TSharedPtr<FJsonValue>> Captures;
    for (const FString& Path : LightingSweepScreenshots)
    {
        TSharedPtr<FJsonObject> Capture = MakeShared<FJsonObject>();
        Capture->SetStringField(TEXT("file"), Path);
        const int64 Size = IFileManager::Get().FileSize(*Path);
        Capture->SetNumberField(TEXT("observed_file_bytes"), Size);
        Capture->SetBoolField(TEXT("nonempty_file_observed"), Size > 0);
        Captures.Add(MakeShared<FJsonValueObject>(Capture));
    }
    LightingSweepReport->SetArrayField(TEXT("screenshot_files"), Captures);
    CurrentResult->SetObjectField(TEXT("lighting_sweep"), LightingSweepReport);
}

void UViewerSmokeTestComponent::RestoreLightingGridOverride()
{
    if (!bLightingGridOverridden) return;
    if (IConsoleVariable* Grid = IConsoleManager::Get().FindConsoleVariable(TEXT("r.Forward.MaxCulledLightsPerCell")))
    {
        Grid->SetWithCurrentPriority(*SavedLightingGridValue);
        bLightingGridRestored = Grid->GetString() == SavedLightingGridValue;
    }
    else bLightingGridRestored = false;
    bLightingGridOverridden = false;
}

void UViewerSmokeTestComponent::BeginRuntimeDiagnostics()
{
    AViewerPawn* Pawn = Viewer.Get();
    ProbeFrames = 0;
    if (!Pawn || !Pawn->Preferences || !SavedPreferences)
    {
        AddCheck(TEXT("settings_roundtrip"), TEXT("unknown"), TEXT("Runtime Preferences or snapshot missing."));
        BeginDynamicsProbe();
        return;
    }
    const TArray<int32> Rates = UViewerPreferences::FrameRates();
    ExpectedFrameRateChoice = Rates.Num() > 1 && Pawn->Preferences->GetChoice(EViewerSetting::FrameRate) == 0 ? 1 : 0;
    ExpectedResolutionChoice = FMath::IsNearlyEqual(Pawn->Preferences->ResolutionPercent, 70.f) ? 1 : 0;
    AddCheck(TEXT("fps_probe_has_alternate_supported_choice"), Rates.Num() > 1 ? TEXT("pass") : TEXT("unknown"),
        TEXT("Only hardware-supported choices are used; one available rate cannot demonstrate a changed-rate roundtrip."));
    Pawn->Preferences->SetChoice(EViewerSetting::FrameRate, ExpectedFrameRateChoice);
    Pawn->Preferences->SetChoice(EViewerSetting::Resolution, ExpectedResolutionChoice);
    ExpectedFrameRate = Pawn->Preferences->FrameRate;
    ExpectedResolution = Pawn->Preferences->ResolutionPercent;
    Pawn->ApplyPreferences(); // Applies production logic without Persist.
    if (Pawn->ViewerUI)
    {
        // OnOpenSettings is the production toggle, so establish a closed
        // state and release its dismissal latch before opening each case.
        if (UFunction* CloseSettings = Pawn->ViewerUI->FindFunction(TEXT("OnCloseSettings")))
            Pawn->ViewerUI->ProcessEvent(CloseSettings, nullptr);
        else AddCheck(TEXT("settings_panel_close_action_available"), TEXT("unknown"));
        Pawn->ViewerUI->ConsumeModalPointer(FVector2D::ZeroVector, false);
        Pawn->ViewerUI->Refresh();
        if (UFunction* OpenSettings = Pawn->ViewerUI->FindFunction(TEXT("OnOpenSettings")))
            Pawn->ViewerUI->ProcessEvent(OpenSettings, nullptr);
        else AddCheck(TEXT("settings_panel_open_action_available"), TEXT("unknown"));
    }
    bPreferencesRestored = false;
    Stage = EStage::SettingsProbe;
    StageStarted = FPlatformTime::Seconds();
}

void UViewerSmokeTestComponent::FinishSettingsProbe()
{
    AViewerPawn* Pawn = Viewer.Get();
    TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
    Detail->SetNumberField(TEXT("requested_fps"), ExpectedFrameRate);
    Detail->SetNumberField(TEXT("requested_resolution_percent"), ExpectedResolution);
    Detail->SetBoolField(TEXT("persist_called"), false);
    if (Pawn && Pawn->Preferences)
    {
        Detail->SetNumberField(TEXT("preference_fps"), Pawn->Preferences->FrameRate);
        Detail->SetNumberField(TEXT("preference_resolution_percent"), Pawn->Preferences->ResolutionPercent);
        AddCheck(TEXT("fps_preference_choice_roundtrip"),
            Pawn->Preferences->GetChoice(EViewerSetting::FrameRate) == ExpectedFrameRateChoice ? TEXT("pass") : TEXT("fail"));
        AddCheck(TEXT("resolution_preference_choice_roundtrip"),
            Pawn->Preferences->GetChoice(EViewerSetting::Resolution) == ExpectedResolutionChoice ? TEXT("pass") : TEXT("fail"));
    }
    for (const TPair<FString, float>& Test : { TPair<FString, float>(TEXT("t.MaxFPS"), ExpectedFrameRate),
        TPair<FString, float>(TEXT("r.ScreenPercentage"), ExpectedResolution) })
    {
        IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(*Test.Key);
        if (!Variable) AddCheck(Test.Key + TEXT(".actual_cvar_roundtrip"), TEXT("unknown"), TEXT("CVar unavailable on this running platform."));
        else
        {
            const float Actual = Variable->GetFloat();
            if (FMath::IsFinite(Actual)) Detail->SetNumberField(Test.Key, Actual);
            AddCheck(Test.Key + TEXT(".actual_cvar_roundtrip"),
                FMath::IsFinite(Actual) && FMath::IsNearlyEqual(Actual, Test.Value, 0.05f) ? TEXT("pass") : TEXT("fail"));
        }
    }
    Detail->SetNumberField(TEXT("platform_frame_pace"), FPlatformRHIFramePacer::GetFramePace());
    Detail->SetStringField(TEXT("render_resolution_scope"), TEXT("Actual runtime screen-percentage CVar checked; internal RHI scene-render dimensions are not inferred from window size."));
    CurrentResult->SetObjectField(TEXT("settings_probe"), Detail);
    InspectViewportAndUi();
    CurrentResult->SetObjectField(TEXT("settings_overlay_viewport_and_ui"), CurrentResult->GetObjectField(TEXT("viewport_and_ui")));
    // Cleanup is unconditional: a failed pointer check must neither reopen
    // the next toggle nor leave an active dismissal press for the next case.
    if (Pawn && Pawn->ViewerUI)
    {
        if (UFunction* CloseSettings = Pawn->ViewerUI->FindFunction(TEXT("OnCloseSettings")))
            Pawn->ViewerUI->ProcessEvent(CloseSettings, nullptr);
        Pawn->ViewerUI->ConsumeModalPointer(FVector2D::ZeroVector, false);
    }
    RestoreUserSettings(false);
}

void UViewerSmokeTestComponent::CheckResetState(const FString& Prefix)
{
    if (!Viewer.IsValid()) return;
    int32 Count = 0;
    for (USkeletalMeshComponent* Part : Viewer->Parts)
        if (UViewerSkeletalMeshComponent* Dynamic = Cast<UViewerSkeletalMeshComponent>(Part))
        {
            const bool Reset = Dynamic->GetActiveDynamicChainCount() == 0 && Dynamic->GetLastSolvedFrame() == MAX_uint64
                && FMath::IsFinite(Dynamic->GetLastMaxDisplacementCm()) && Dynamic->GetLastMaxDisplacementCm() == 0.f;
            AddCheck(Prefix + FString::Printf(TEXT(".component[%d].state_cleared"), Count++), Reset ? TEXT("pass") : TEXT("fail"));
        }
    if (!Count) AddCheck(Prefix + TEXT(".state_cleared"), TEXT("unknown"), TEXT("No reconstructed dynamics component exists in this case."));
}

void UViewerSmokeTestComponent::BeginDynamicsProbe()
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn) return;
    DynamicObservations.Reset();
    ProbeFrames = 0;
    if (Pawn->Preferences)
    {
        Pawn->Preferences->SetToggle(EViewerSetting::SecondaryMotion, true);
        Pawn->ApplyPreferences();
        bPreferencesRestored = false;
    }
    Pawn->ResetView();
    CheckResetState(TEXT("selection_reset"));
    SceneRelativeTransforms.Reset();
    MaxSceneTranslationErrorCm = MaxSceneRotationErrorDegrees = 0.f;
    bSceneStayedWithCharacter = true;
    const FTransform View = Pawn->Camera ? Pawn->Camera->GetComponentTransform() : FTransform::Identity;
    const FTransform ModelInView = Pawn->ModelRoot->GetComponentTransform().GetRelativeTransform(View);
    for (UStaticMeshComponent* Mesh : Pawn->StageMeshes)
        SceneRelativeTransforms.Add(Mesh->GetComponentTransform().GetRelativeTransform(View).GetRelativeTransform(ModelInView));
    const FViewerOutfit* Outfit = Pawn->GetCurrentOutfit();
    for (int32 Index = 0; Index < Pawn->Parts.Num(); ++Index)
    {
        UViewerSkeletalMeshComponent* Component = Cast<UViewerSkeletalMeshComponent>(Pawn->Parts[Index]);
        if (!Component) continue;
        if (Outfit && Outfit->Parts.IsValidIndex(Index) && Outfit->Parts[Index].Dynamics.IsNull())
        {
            AddCheck(FString::Printf(TEXT("dynamics.part[%d].source_profile_declared"), Index), TEXT("not_applicable"),
                TEXT("Catalog declares no secondary-motion profile for this part; no solver response is expected."));
            continue;
        }
        FDynamicObservation Observation;
        Observation.Component = Component;
        Observation.PartIndex = Index;
        Observation.PreviousRotation = Component->GetComponentQuat();
        Observation.PreviousSolvedFrame = Component->GetLastSolvedFrame();
        if (Outfit && Outfit->Parts.IsValidIndex(Index))
            if (const UViewerDynamicsProfile* Profile = Outfit->Parts[Index].Dynamics.Get())
                for (const FViewerDynamicChain& Chain : Profile->Chains)
                    Observation.SafetyLimitCm = FMath::Max(Observation.SafetyLimitCm, FMath::Clamp(Chain.MaxDisplacementCm, 0.1f, 50.f));
        DynamicObservations.Add(Observation);
    }
    Stage = EStage::DrivingDynamics;
    StageStarted = FPlatformTime::Seconds();
}

void UViewerSmokeTestComponent::SampleDynamics()
{
    if (AViewerPawn* Pawn = Viewer.Get())
    {
        bSceneStayedWithCharacter &= Pawn->StageMeshes.Num() == SceneRelativeTransforms.Num();
        const FTransform View = Pawn->Camera ? Pawn->Camera->GetComponentTransform() : FTransform::Identity;
        const FTransform ModelInView = Pawn->ModelRoot->GetComponentTransform().GetRelativeTransform(View);
        for (int32 Index = 0; Index < Pawn->StageMeshes.Num() && Index < SceneRelativeTransforms.Num(); ++Index)
        {
            const FTransform Relative = Pawn->StageMeshes[Index]->GetComponentTransform()
                .GetRelativeTransform(View).GetRelativeTransform(ModelInView);
            const float TranslationError = FVector::Distance(Relative.GetLocation(), SceneRelativeTransforms[Index].GetLocation());
            const float RotationError = FMath::RadiansToDegrees(Relative.GetRotation()
                .AngularDistance(SceneRelativeTransforms[Index].GetRotation()));
            bSceneStayedWithCharacter &= FMath::IsFinite(TranslationError) && FMath::IsFinite(RotationError)
                && TranslationError < 0.1f && RotationError < 0.05f;
            MaxSceneTranslationErrorCm = FMath::Max(MaxSceneTranslationErrorCm, TranslationError);
            MaxSceneRotationErrorDegrees = FMath::Max(MaxSceneRotationErrorDegrees, RotationError);
        }
    }
    for (FDynamicObservation& Observation : DynamicObservations)
        if (UViewerSkeletalMeshComponent* Component = Observation.Component.Get())
        {
            ++Observation.Samples;
            const uint64 Frame = Component->GetLastSolvedFrame();
            if (Frame != MAX_uint64 && Frame != Observation.PreviousSolvedFrame) ++Observation.SolvedFrameChanges;
            Observation.PreviousSolvedFrame = Frame;
            Observation.MaxActiveChains = FMath::Max(Observation.MaxActiveChains, Component->GetActiveDynamicChainCount());
            const float Displacement = Component->GetLastMaxDisplacementCm();
            Observation.bFinite &= FMath::IsFinite(Displacement) && Displacement >= 0.f;
            if (FMath::IsFinite(Displacement)) Observation.PeakDisplacementCm = FMath::Max(Observation.PeakDisplacementCm, Displacement);
            const FQuat Rotation = Component->GetComponentQuat();
            const double Angle = FMath::RadiansToDegrees(Observation.PreviousRotation.AngularDistance(Rotation));
            Observation.bFinite &= FMath::IsFinite(Angle);
            if (FMath::IsFinite(Angle)) Observation.DrivenRotationDegrees += Angle;
            Observation.PreviousRotation = Rotation;
        }
}

void UViewerSmokeTestComponent::FinishDynamicsProbe()
{
    CurrentResult->SetNumberField(TEXT("max_scene_relative_translation_error_cm"), MaxSceneTranslationErrorCm);
    CurrentResult->SetNumberField(TEXT("max_scene_relative_rotation_error_degrees"), MaxSceneRotationErrorDegrees);
    AddCheck(TEXT("scene_and_character_preserve_relative_placement_during_turn"),
        !SceneRelativeTransforms.IsEmpty() && bSceneStayedWithCharacter ? TEXT("pass") : TEXT("fail"),
        TEXT("Scene and avatar transforms are first expressed relative to the measured camera, then their relative placement is compared. This checks visual scene/avatar placement for camera orbit or legacy world turns; it does not require world geometry to rotate."));
    const bool LegacyTurntable = FParse::Param(FCommandLine::Get(), TEXT("ViewerLegacyTurntable"));
    TArray<TSharedPtr<FJsonValue>> Observations;
    int32 MatchedComponents = 0;
    for (const FDynamicObservation& Observation : DynamicObservations)
    {
        TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
        Detail->SetNumberField(TEXT("part_index"), Observation.PartIndex);
        Detail->SetNumberField(TEXT("active_chain_count_max"), Observation.MaxActiveChains);
        Detail->SetNumberField(TEXT("sample_count"), Observation.Samples);
        Detail->SetNumberField(TEXT("solved_frame_changes"), Observation.SolvedFrameChanges);
        Detail->SetStringField(TEXT("last_observed_solved_frame"), Observation.PreviousSolvedFrame == MAX_uint64
            ? TEXT("not_solved") : LexToString(Observation.PreviousSolvedFrame));
        Detail->SetNumberField(TEXT("actual_driven_rotation_degrees"), Observation.DrivenRotationDegrees);
        Detail->SetNumberField(TEXT("peak_actual_displacement_cm"), Observation.PeakDisplacementCm);
        Detail->SetNumberField(TEXT("profile_safety_displacement_limit_cm"), Observation.SafetyLimitCm);
        Detail->SetBoolField(TEXT("all_diagnostics_finite"), Observation.bFinite);
        const FString Prefix = FString::Printf(TEXT("dynamics.part[%d]."), Observation.PartIndex);
        AddCheck(Prefix + TEXT("finite_diagnostics"), Observation.bFinite ? TEXT("pass") : TEXT("fail"));
        if (Observation.MaxActiveChains <= 0)
        {
            AddCheck(Prefix + TEXT("driven_response"), TEXT("unknown"), TEXT("No matched active dynamic chain; this part is not treated as a passed dynamics test."));
            Detail->SetStringField(TEXT("response_status"), TEXT("unknown_no_matching_chain"));
        }
        else
        {
            ++MatchedComponents;
            AddCheck(Prefix + TEXT("solver_frame_advanced"), Observation.SolvedFrameChanges >= 3 ? TEXT("pass") : TEXT("fail"));
            const bool Driven = Observation.DrivenRotationDegrees > 5.f && Observation.Samples >= 8;
            AddCheck(Prefix + TEXT("actual_turn_driver_observed"), !LegacyTurntable ? TEXT("not_applicable") : Driven ? TEXT("pass") : TEXT("unknown"));
            const bool Responded = Observation.PeakDisplacementCm > 0.0001f;
            AddCheck(Prefix + TEXT("driven_response"), !LegacyTurntable ? TEXT("not_applicable") : Driven && Responded ? TEXT("pass") : TEXT("unknown"),
                TEXT("Camera orbit does not turn the physical component and does not test turn-induced inertia. Legacy response requires a measured component turn and a nonzero finite solver displacement."));
            const bool Bounded = Observation.SafetyLimitCm > 0.f && Observation.PeakDisplacementCm <= Observation.SafetyLimitCm + 0.1f;
            AddCheck(Prefix + TEXT("bounded_response"), Bounded ? TEXT("pass") : Observation.SafetyLimitCm <= 0.f ? TEXT("unknown") : TEXT("fail"));
            Detail->SetStringField(TEXT("response_status"), !LegacyTurntable ? TEXT("not_applicable_camera_orbit") : Driven && Responded && Observation.bFinite ? TEXT("observed") : TEXT("unknown"));
        }
        Observations.Add(MakeShared<FJsonValueObject>(Detail));
    }
    CurrentResult->SetArrayField(TEXT("driven_dynamics"), Observations);
    CurrentResult->SetStringField(TEXT("driver"), LegacyTurntable
        ? TEXT("1.2 second +/-32 degree sinusoidal Pawn.Yaw target driving the legacy SceneRoot turntable; no teleport or simulated-result injection.")
        : TEXT("1.2 second +/-32 degree sinusoidal Pawn.Yaw target driving the camera around fixed world geometry. Solver bounds/frame progression are observed; turn-induced physical response is not applicable."));
    AddCheck(TEXT("dynamics_has_matched_chain"), MatchedComponents > 0 ? TEXT("pass") : TEXT("unknown"));
}

void UViewerSmokeTestComponent::CheckDisabledDynamicsPose()
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn) return;
    TArray<TSharedPtr<FJsonValue>> ResultsArray;
    for (int32 Index = 0; Index < Pawn->Parts.Num(); ++Index)
    {
        UViewerSkeletalMeshComponent* Component = Cast<UViewerSkeletalMeshComponent>(Pawn->Parts[Index]);
        if (!Component || !Component->GetSkeletalMeshAsset()) continue;
        const FString Prefix = FString::Printf(TEXT("disabled_dynamics.part[%d]."), Index);
        const bool Cleared = !Component->IsSecondaryMotionEnabled() && Component->GetActiveDynamicChainCount() == 0
            && Component->GetLastSolvedFrame() == MAX_uint64 && Component->GetLastMaxDisplacementCm() == 0.f;
        AddCheck(Prefix + TEXT("disabled_and_solver_state_stays_clear"), Cleared ? TEXT("pass") : TEXT("fail"));
        // The reconstruction edits pending component-space transforms only.
        // Public local animation transforms are an independent unmodified base
        // for animated parts; reference-pose parts use the mesh reference pose.
        const FReferenceSkeleton& Ref = Component->GetSkeletalMeshAsset()->GetRefSkeleton();
        const bool Animated = Component->GetSingleNodeInstance() && Component->GetSingleNodeInstance()->GetCurrentAsset();
        TArray<FTransform> LocalPose = Animated ? Component->GetBoneSpaceTransforms() : Ref.GetRefBonePose();
        const TArray<FTransform>& PublishedPose = Component->GetComponentSpaceTransforms();
        bool Comparable = LocalPose.Num() == Ref.GetNum() && PublishedPose.Num() == Ref.GetNum();
        bool Finite = true;
        double PositionError = 0.0, RotationError = 0.0, ScaleError = 0.0;
        if (Comparable)
        {
            TArray<FTransform> Expected;
            Expected.SetNumUninitialized(LocalPose.Num());
            for (int32 Bone = 0; Bone < LocalPose.Num(); ++Bone)
            {
                const int32 Parent = Ref.GetParentIndex(Bone);
                Expected[Bone] = Parent >= 0 ? LocalPose[Bone] * Expected[Parent] : LocalPose[Bone];
                Expected[Bone].NormalizeRotation();
                Finite &= !Expected[Bone].ContainsNaN() && !PublishedPose[Bone].ContainsNaN();
                PositionError = FMath::Max(PositionError, FVector::Distance(Expected[Bone].GetTranslation(), PublishedPose[Bone].GetTranslation()));
                RotationError = FMath::Max(RotationError, FMath::RadiansToDegrees(Expected[Bone].GetRotation().AngularDistance(PublishedPose[Bone].GetRotation())));
                ScaleError = FMath::Max(ScaleError, FVector::Distance(Expected[Bone].GetScale3D(), PublishedPose[Bone].GetScale3D()));
            }
        }
        const bool Restored = Finite && PositionError < 0.05 && RotationError < 0.05 && ScaleError < 0.001;
        AddCheck(Prefix + TEXT("published_pose_matches_unmodified_base"), !Comparable ? TEXT("unknown") : Restored ? TEXT("pass") : TEXT("fail"));
        TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
        Detail->SetNumberField(TEXT("part_index"), Index);
        Detail->SetStringField(TEXT("baseline"), Animated ? TEXT("public_unmodified_local_animation_transforms") : TEXT("mesh_reference_pose"));
        Detail->SetBoolField(TEXT("pose_arrays_comparable"), Comparable);
        Detail->SetBoolField(TEXT("finite"), Finite);
        if (Finite && FMath::IsFinite(PositionError) && FMath::IsFinite(RotationError) && FMath::IsFinite(ScaleError))
        {
            Detail->SetNumberField(TEXT("max_position_error_cm"), PositionError);
            Detail->SetNumberField(TEXT("max_rotation_error_degrees"), RotationError);
            Detail->SetNumberField(TEXT("max_scale_error"), ScaleError);
        }
        ResultsArray.Add(MakeShared<FJsonValueObject>(Detail));
    }
    CurrentResult->SetArrayField(TEXT("disabled_pose_comparison"), ResultsArray);
}

void UViewerSmokeTestComponent::FinishReplayProbe()
{
    TArray<TSharedPtr<FJsonValue>> ReplayDiagnostics;
    for (const FDynamicObservation& Observation : DynamicObservations)
        if (UViewerSkeletalMeshComponent* Component = Observation.Component.Get())
        {
            TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
            Detail->SetNumberField(TEXT("part_index"), Observation.PartIndex);
            Detail->SetNumberField(TEXT("previously_observed_chain_count_max"), Observation.MaxActiveChains);
            Detail->SetNumberField(TEXT("active_chain_count_after_replay"), Component->GetActiveDynamicChainCount());
            Detail->SetBoolField(TEXT("secondary_motion_enabled"), Component->IsSecondaryMotionEnabled());
            Detail->SetStringField(TEXT("last_solved_frame"), Component->GetLastSolvedFrame() == MAX_uint64
                ? TEXT("not_solved") : LexToString(Component->GetLastSolvedFrame()));
            Detail->SetNumberField(TEXT("seconds_since_replay_reset"), FPlatformTime::Seconds() - StageStarted);
            const float Position = Component->GetPosition();
            if (FMath::IsFinite(Position)) Detail->SetNumberField(TEXT("animation_position_seconds"), Position);
            ReplayDiagnostics.Add(MakeShared<FJsonValueObject>(Detail));
            const FString Prefix = FString::Printf(TEXT("replay.part[%d]."), Observation.PartIndex);
            AddCheck(Prefix + TEXT("enabled_after_replay"), Component->IsSecondaryMotionEnabled() ? TEXT("pass") : TEXT("fail"));
            AddCheck(Prefix + TEXT("solver_resumed"), Observation.MaxActiveChains <= 0 ? TEXT("unknown")
                : Component->GetLastSolvedFrame() != MAX_uint64 && Component->GetActiveDynamicChainCount() > 0 ? TEXT("pass") : TEXT("fail"));
        }
    CurrentResult->SetArrayField(TEXT("replay_dynamics"), ReplayDiagnostics);
}

void UViewerSmokeTestComponent::InspectViewportAndUi()
{
    AViewerPawn* Pawn = Viewer.Get();
    if (!Pawn) return;
    const FIntPoint Size = GEngine && GEngine->GameViewport && GEngine->GameViewport->Viewport
        ? GEngine->GameViewport->Viewport->GetSizeXY() : FIntPoint::ZeroValue;
    TSharedPtr<FJsonObject> Detail = MakeShared<FJsonObject>();
    TSharedPtr<FJsonObject> Rendering = MakeShared<FJsonObject>();
    Rendering->SetStringField(TEXT("rhi_name"), GDynamicRHI ? GDynamicRHI->GetName() : TEXT("unavailable"));
    Rendering->SetBoolField(TEXT("using_null_rhi"), GUsingNullRHI);
    Rendering->SetBoolField(TEXT("unreal_rhi_supports_ray_tracing"), GRHISupportsRayTracing);
    Rendering->SetBoolField(TEXT("unreal_ray_tracing_enabled"), IsRayTracingEnabled());
    Rendering->SetStringField(TEXT("maximum_rhi_shader_platform"), LexToString(GMaxRHIShaderPlatform, false));
    Rendering->SetBoolField(TEXT("stage_fog_component_created"), Pawn->StageFog != nullptr);
    if (Pawn->StageFog)
    {
        Rendering->SetBoolField(TEXT("stage_fog_visible"), Pawn->StageFog->IsVisible());
        Rendering->SetBoolField(TEXT("stage_fog_requests_fsss"), Pawn->StageFog->bEnableFSSS);
        Rendering->SetNumberField(TEXT("stage_fog_density"), Pawn->StageFog->FogDensity);
    }
    if (Pawn->Preferences)
    {
        Rendering->SetNumberField(TEXT("preference_antialiasing_samples"), Pawn->Preferences->Antialiasing);
        Rendering->SetBoolField(TEXT("preference_screen_space_reflections"), Pawn->Preferences->bScreenSpaceReflections);
    }
    if (const UWorld* World = Pawn->GetWorld())
        if (const FSceneInterface* Scene = World->Scene)
        {
            Rendering->SetStringField(TEXT("scene_shader_platform"), LexToString(Scene->GetShaderPlatform(), false));
            Rendering->SetNumberField(TEXT("scene_feature_level"), static_cast<int32>(Scene->GetFeatureLevel()));
            FString FeatureLevelName;
            GetFeatureLevelName(Scene->GetFeatureLevel(), FeatureLevelName);
            Rendering->SetStringField(TEXT("scene_feature_level_name"), FeatureLevelName);
        }
    TSharedPtr<FJsonObject> NativeCapabilities;
    const FString NativeJson = UViewerGraphicsCapabilities::ReportJson();
    if (FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(NativeJson), NativeCapabilities) && NativeCapabilities.IsValid())
        Rendering->SetObjectField(TEXT("native_graphics_capabilities"), NativeCapabilities);
    else Rendering->SetStringField(TEXT("native_graphics_capabilities_json"), NativeJson);
    if (Pawn->StagePostProcess)
    {
        const FPostProcessSettings& Post = Pawn->StagePostProcess->Settings;
        Rendering->SetBoolField(TEXT("stage_overrides_global_illumination_method"), Post.bOverride_DynamicGlobalIlluminationMethod);
        Rendering->SetNumberField(TEXT("stage_global_illumination_method"), static_cast<int32>(Post.DynamicGlobalIlluminationMethod.GetValue()));
        Rendering->SetBoolField(TEXT("stage_overrides_reflection_method"), Post.bOverride_ReflectionMethod);
        Rendering->SetNumberField(TEXT("stage_reflection_method"), static_cast<int32>(Post.ReflectionMethod.GetValue()));
        Rendering->SetNumberField(TEXT("stage_ssr_intensity"), Post.ScreenSpaceReflectionIntensity);
        Rendering->SetNumberField(TEXT("stage_ssr_quality"), Post.ScreenSpaceReflectionQuality);
        Rendering->SetNumberField(TEXT("stage_ssr_max_roughness"), Post.ScreenSpaceReflectionMaxRoughness);
    }
    TSharedPtr<FJsonObject> RenderCVars = MakeShared<FJsonObject>();
    for (const TCHAR* Name : { TEXT("r.DynamicGlobalIlluminationMethod"), TEXT("r.ReflectionMethod"),
        TEXT("r.Lumen.DiffuseIndirect.Allow"), TEXT("r.Lumen.Reflections.Allow"), TEXT("r.GenerateMeshDistanceFields"),
        TEXT("r.RayTracing"), TEXT("r.SkinCache.CompileShaders"), TEXT("r.Lumen.HardwareRayTracing"),
        TEXT("r.RayTracing.Geometry.SkeletalMeshes"), TEXT("r.Mobile.AntiAliasing"), TEXT("r.AntiAliasingMethod"),
        TEXT("r.MSAACount"), TEXT("r.Mobile.ScreenSpaceReflections"), TEXT("r.SSR.Quality"),
        TEXT("r.SSR.HalfResSceneColor"), TEXT("r.Mobile.AmbientOcclusion"),
        TEXT("r.ForwardShading"), TEXT("r.MegaLights.EnableForProject"), TEXT("r.MegaLights.Allow"),
        TEXT("r.MegaLights.FrontLayerTranslucency.EnableForProject"),
        TEXT("r.Fog.SeparateComposition"), TEXT("r.VolumetricFog"), TEXT("r.SSS.Quality") })
        if (const IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(Name))
            RenderCVars->SetStringField(Name, Variable->GetString());
    Rendering->SetObjectField(TEXT("runtime_console_variables"), RenderCVars);
    Rendering->SetStringField(TEXT("scope"), TEXT("Actual running scene/RHI, native Metal queries, and component settings. These observations do not prove a particular render pass ran or establish pixel appearance; screenshots still require visual review."));
    Detail->SetObjectField(TEXT("rendering"), Rendering);
    Detail->SetNumberField(TEXT("actual_viewport_width_pixels"), Size.X);
    Detail->SetNumberField(TEXT("actual_viewport_height_pixels"), Size.Y);
    AddCheck(TEXT("actual_viewport_has_nonzero_dimensions"), Size.X > 0 && Size.Y > 0 ? TEXT("pass") : TEXT("unknown"));
    if (Pawn->Camera)
    {
        Detail->SetBoolField(TEXT("camera_constrains_aspect_ratio"), Pawn->Camera->bConstrainAspectRatio);
        if (FMath::IsFinite(Pawn->Camera->AspectRatio)) Detail->SetNumberField(TEXT("camera_aspect_ratio"), Pawn->Camera->AspectRatio);
        if (FMath::IsFinite(Pawn->Camera->FieldOfView)) Detail->SetNumberField(TEXT("camera_horizontal_fov"), Pawn->Camera->FieldOfView);
        AddCheck(TEXT("camera_projection_finite"), FMath::IsFinite(Pawn->Camera->AspectRatio)
            && FMath::IsFinite(Pawn->Camera->FieldOfView) && Pawn->Camera->FieldOfView > 0.f ? TEXT("pass") : TEXT("fail"));
        AddCheck(TEXT("camera_has_no_fixed_aspect_black_frame"), !Pawn->Camera->bConstrainAspectRatio ? TEXT("pass") : TEXT("fail"));
        const double Aspect = Size.Y > 0 ? double(Size.X) / Size.Y : 0.0;
        AddCheck(TEXT("camera_aspect_matches_actual_viewport"), Aspect <= 0.0 ? TEXT("unknown")
            : FMath::IsNearlyEqual(double(Pawn->Camera->AspectRatio), Aspect, 0.01) ? TEXT("pass") : TEXT("fail"));
    }
    else AddCheck(TEXT("camera_has_no_fixed_aspect_black_frame"), TEXT("unknown"), TEXT("No camera component."));
    TArray<TSharedPtr<FJsonValue>> GeometryResults;
    UBorder* ObservedSettingsPanel = nullptr;
    int32 Checked = 0;
    if (Pawn->ViewerUI && Pawn->ViewerUI->WidgetTree && Size.X > 0 && Size.Y > 0)
        Pawn->ViewerUI->WidgetTree->ForEachWidget([&](UWidget* Widget)
        {
            // Discover the rendered settings panel through its public picker
            // ancestry instead of depending on private members or auto names.
            if (!ObservedSettingsPanel && Cast<UViewerSettingPicker>(Widget))
                for (UWidget* Ancestor = Widget->GetParent(); Ancestor; Ancestor = Ancestor->GetParent())
                    if (UBorder* Border = Cast<UBorder>(Ancestor)) { ObservedSettingsPanel = Border; break; }
            if (!Widget || !(Widget->IsA<UButton>() || Widget->IsA<UBorder>() || Widget->IsA<UScrollBox>() || Widget->IsA<USafeZone>())) return;
            bool WithinScroll = false;
            for (UWidget* Ancestor = Widget; Ancestor; Ancestor = Ancestor->GetParent())
            {
                if (!Ancestor->IsVisible()) return;
                if (Ancestor != Widget && Ancestor->IsA<UScrollBox>()) WithinScroll = true;
            }
            // Offscreen portrait/setting rows inside a ScrollBox are deliberately
            // clipped. Validate the enclosing scroll viewport, not hidden rows.
            if (WithinScroll) return;
            const FGeometry& Geometry = Widget->GetCachedGeometry();
            if (Geometry.GetLocalSize().GetMin() <= 0.f) return;
            FVector2D TopLeft, BottomRight, Logical;
            USlateBlueprintLibrary::LocalToViewport(this, Geometry, FVector2D::ZeroVector, TopLeft, Logical);
            USlateBlueprintLibrary::LocalToViewport(this, Geometry, Geometry.GetLocalSize(), BottomRight, Logical);
            const bool Finite = FMath::IsFinite(TopLeft.X) && FMath::IsFinite(TopLeft.Y)
                && FMath::IsFinite(BottomRight.X) && FMath::IsFinite(BottomRight.Y);
            const bool Inside = Finite && TopLeft.X >= -2.0 && TopLeft.Y >= -2.0
                && BottomRight.X <= Size.X + 2.0 && BottomRight.Y <= Size.Y + 2.0;
            TSharedPtr<FJsonObject> Area = MakeShared<FJsonObject>();
            Area->SetStringField(TEXT("widget"), Widget->GetName());
            Area->SetStringField(TEXT("class"), Widget->GetClass()->GetName());
            Area->SetBoolField(TEXT("within_viewport"), Inside);
            if (Finite)
            {
                Area->SetArrayField(TEXT("pixel_rect"), {MakeShared<FJsonValueNumber>(TopLeft.X), MakeShared<FJsonValueNumber>(TopLeft.Y),
                    MakeShared<FJsonValueNumber>(BottomRight.X), MakeShared<FJsonValueNumber>(BottomRight.Y)});
            }
            GeometryResults.Add(MakeShared<FJsonValueObject>(Area));
            AddCheck(FString::Printf(TEXT("ui.area[%d].within_actual_viewport"), Checked++), Inside ? TEXT("pass") : TEXT("fail"));
        });
    AddCheck(TEXT("ui_geometry_observed"), Checked > 0 ? TEXT("pass") : TEXT("unknown"));
    Detail->SetArrayField(TEXT("visible_ui_areas"), GeometryResults);
    Detail->SetStringField(TEXT("foldable_scope"), TEXT("Records the actual current viewport and visible UI geometry; a physical fold/unfold event is not fabricated by this test."));
    CurrentResult->SetObjectField(TEXT("viewport_and_ui"), Detail);
    if (Stage != EStage::SettingsProbe) return;
    bool bPanelVisible = ObservedSettingsPanel != nullptr;
    for (UWidget* Ancestor = ObservedSettingsPanel; Ancestor; Ancestor = Ancestor->GetParent())
        bPanelVisible &= Ancestor->IsVisible();
    AddCheck(TEXT("settings_panel_visible_before_pointer_probe"), !Pawn->ViewerUI || !ObservedSettingsPanel
        ? TEXT("unknown") : bPanelVisible ? TEXT("pass") : TEXT("fail"),
        TEXT("The measured settings border and its ancestors must be visible; hidden cached geometry cannot establish an open modal."));
    if (!Pawn->ViewerUI || !ObservedSettingsPanel || !bPanelVisible || Size.X <= 0 || Size.Y <= 0)
    {
        AddCheck(TEXT("settings_outside_pointer_closes_modal"), TEXT("unknown"), TEXT("No measured settings panel geometry."));
        if (Pawn->ViewerUI)
            if (UFunction* Close = Pawn->ViewerUI->FindFunction(TEXT("OnCloseSettings"))) Pawn->ViewerUI->ProcessEvent(Close, nullptr);
        return;
    }
    const FGeometry& PanelGeometry = ObservedSettingsPanel->GetCachedGeometry();
    FVector2D PanelTopLeft, PanelBottomRight, Logical;
    USlateBlueprintLibrary::LocalToViewport(this, PanelGeometry, FVector2D::ZeroVector, PanelTopLeft, Logical);
    USlateBlueprintLibrary::LocalToViewport(this, PanelGeometry, PanelGeometry.GetLocalSize(), PanelBottomRight, Logical);
    const FVector2D Inside = (PanelTopLeft + PanelBottomRight) * 0.5;
    const TArray<float> Gaps{ static_cast<float>(PanelTopLeft.X), static_cast<float>(Size.X - PanelBottomRight.X),
        static_cast<float>(PanelTopLeft.Y), static_cast<float>(Size.Y - PanelBottomRight.Y) };
    int32 Side = 0;
    for (int32 Index = 1; Index < Gaps.Num(); ++Index) if (Gaps[Index] > Gaps[Side]) Side = Index;
    FVector2D Outside = Inside;
    if (Side == 0) Outside.X = PanelTopLeft.X * 0.5;
    else if (Side == 1) Outside.X = (PanelBottomRight.X + Size.X) * 0.5;
    else if (Side == 2) Outside.Y = PanelTopLeft.Y * 0.5;
    else Outside.Y = (PanelBottomRight.Y + Size.Y) * 0.5;
    const bool Measured = PanelGeometry.GetLocalSize().GetMin() > 0.f && Gaps[Side] > 2.f
        && FMath::IsFinite(PanelTopLeft.X) && FMath::IsFinite(PanelTopLeft.Y)
        && FMath::IsFinite(PanelBottomRight.X) && FMath::IsFinite(PanelBottomRight.Y)
        && FMath::IsFinite(Outside.X) && FMath::IsFinite(Outside.Y)
        && Outside.X >= 0.f && Outside.X < Size.X && Outside.Y >= 0.f && Outside.Y < Size.Y;
    if (!Measured)
    {
        AddCheck(TEXT("settings_outside_pointer_closes_modal"), TEXT("unknown"), TEXT("No finite viewport point outside the measured panel."));
        if (UFunction* Close = Pawn->ViewerUI->FindFunction(TEXT("OnCloseSettings"))) Pawn->ViewerUI->ProcessEvent(Close, nullptr);
        return;
    }
    // Clear any previous dismissal latch without activating controls. A second
    // inside release must still be consumed by the currently open modal.
    Pawn->ViewerUI->ConsumeModalPointer(Inside, false);
    const bool ModalWasOpen = Pawn->ViewerUI->ConsumeModalPointer(Inside, false);
    const bool PressConsumed = Pawn->ViewerUI->ConsumeModalPointer(Outside, true);
    const bool HeldConsumed = Pawn->ViewerUI->ConsumeModalPointer(Outside, true);
    const bool ReleaseConsumed = Pawn->ViewerUI->ConsumeModalPointer(Outside, false);
    const bool ModalStillOpen = Pawn->ViewerUI->ConsumeModalPointer(Outside, false);
    AddCheck(TEXT("settings_panel_inside_pointer_preserves_modal"), ModalWasOpen ? TEXT("pass") : TEXT("fail"));
    AddCheck(TEXT("settings_outside_pointer_closes_modal"), ModalWasOpen && !ModalStillOpen ? TEXT("pass") : TEXT("fail"));
    AddCheck(TEXT("settings_dismissal_consumes_press_hold_and_release"), PressConsumed && HeldConsumed && ReleaseConsumed ? TEXT("pass") : TEXT("fail"));
    TSharedPtr<FJsonObject> PointerProbe = MakeShared<FJsonObject>();
    PointerProbe->SetArrayField(TEXT("panel_pixel_rect"), { MakeShared<FJsonValueNumber>(PanelTopLeft.X), MakeShared<FJsonValueNumber>(PanelTopLeft.Y),
        MakeShared<FJsonValueNumber>(PanelBottomRight.X), MakeShared<FJsonValueNumber>(PanelBottomRight.Y) });
    PointerProbe->SetArrayField(TEXT("outside_viewport_pixel_position"), { MakeShared<FJsonValueNumber>(Outside.X), MakeShared<FJsonValueNumber>(Outside.Y) });
    PointerProbe->SetStringField(TEXT("scope"), TEXT("Measured runtime widget geometry and production ConsumeModalPointer path; no native touch/Slate event injection or dropdown-popup interaction is claimed."));
    CurrentResult->SetObjectField(TEXT("settings_outside_pointer_probe"), PointerProbe);
}
