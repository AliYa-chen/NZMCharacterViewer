#include "ViewerPawn.h"
#include "ViewerWidget.h"
#include "ViewerSmokeTestComponent.h"
#include "ViewerEnvironment.h"
#include "ViewerDynamics.h"
#include "Camera/CameraComponent.h"
#include "Animation/AnimSequence.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/InputComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/LightComponent.h"
#include "Components/PostProcessComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/AssetManager.h"
#include "Engine/StreamableManager.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Engine/Texture.h"
#include "Engine/TextureCube.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "Materials/MaterialInterface.h"
#include "MaterialShaderPrecompileMode.h"
#include "HAL/PlatformTime.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

AViewerPawn::AViewerPawn()
{
    PrimaryActorTick.bCanEverTick = true;
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    ModelRoot = CreateDefaultSubobject<USceneComponent>(TEXT("ModelRoot"));
    ModelRoot->SetupAttachment(RootComponent);
    PreparingRoot = CreateDefaultSubobject<USceneComponent>(TEXT("PreparingRoot"));
    PreparingRoot->SetupAttachment(RootComponent);
    Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
    Camera->SetupAttachment(RootComponent);
    Camera->FieldOfView = 29.9281126f;
    Camera->bConstrainAspectRatio = false;
    Camera->bOverrideAspectRatioAxisConstraint = true;
    Camera->AspectRatioAxisConstraint = AspectRatio_MaintainXFOV;
    AutoPossessPlayer = EAutoReceiveInput::Player0;
}

void AViewerPawn::BeginPlay()
{
    Super::BeginPlay();
    SetActorLocationAndRotation(FVector::ZeroVector, FRotator::ZeroRotator);
    Preferences = UViewerPreferences::Load();
    Environment = LoadObject<UViewerEnvironment>(nullptr,
        TEXT("/Game/Viewer/Environment/DA_Environment.DA_Environment"));
    if (!Environment)
        UE_LOG(LogTemp, Error, TEXT("Original role environment is missing. Run Scripts/refresh_assets.py; the temporary stage does not reproduce Scene103."));
    ApplyPreferences();
    Catalog = LoadObject<UViewerCatalog>(nullptr, TEXT("/Game/Viewer/Data/DA_ViewerCatalog.DA_ViewerCatalog"));
    LoadSelection();
    InitializeInterface();
    UpdateCamera();
#if !UE_BUILD_SHIPPING
    if (FParse::Param(FCommandLine::Get(), TEXT("ViewerSmokeTest")))
    {
        SmokeTest = NewObject<UViewerSmokeTestComponent>(this);
        SmokeTest->RegisterComponent();
        SmokeTest->Start(this);
    }
#endif
}

void AViewerPawn::InitializeInterface()
{
    if (ViewerUI) return;
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        PC->bShowMouseCursor = true;
        PC->bEnableTouchEvents = true;
        FInputModeGameAndUI Mode;
        Mode.SetHideCursorDuringCapture(false);
        Mode.SetLockMouseToViewportBehavior(EMouseLockMode::DoNotLock);
        PC->SetInputMode(Mode);
        ViewerUI = CreateWidget<UViewerWidget>(PC, UViewerWidget::StaticClass());
        if (ViewerUI)
        {
            ViewerUI->InitializeViewer(this);
            ViewerUI->AddToViewport();
        }
    }
}

void AViewerPawn::SetupPlayerInputComponent(UInputComponent* Input)
{
    Super::SetupPlayerInputComponent(Input);
    Input->BindAxisKey(EKeys::MouseWheelAxis, this, &AViewerPawn::Zoom);
}

void AViewerPawn::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    InitializeInterface();
    TickSelectionPreparation();
    if (!bPreparingSelection && (!SmokeTest || !SmokeTest->IsRunning())) ReadPointerInput();
    DisplayYaw = FMath::FixedTurn(DisplayYaw, Yaw, FMath::Min(DeltaSeconds, 0.05f) * 540.f);
    ModelRoot->SetRelativeRotation(FRotator(0.f, DisplayYaw, 0.f));
    UpdateCamera();
}

const FViewerOutfit* AViewerPawn::GetCurrentOutfit() const
{
    if (!Catalog || !Catalog->Characters.IsValidIndex(CharacterIndex)) return nullptr;
    const FViewerCharacter& Character = Catalog->Characters[CharacterIndex];
    return Character.Outfits.IsValidIndex(OutfitIndex) ? &Character.Outfits[OutfitIndex] : nullptr;
}

void AViewerPawn::ReleaseSelection()
{
    for (USkeletalMeshComponent* Part : Parts)
    {
        if (!Part) continue;
        Part->Stop();
        Part->SetAnimation(nullptr);
        Part->SetSkeletalMesh(nullptr);
        Part->EmptyOverrideMaterials();
        Part->DestroyComponent();
    }
    Parts.Reset();
    if (ActiveHandle) ActiveHandle->ReleaseHandle();
    ActiveHandle.Reset();
}

void AViewerPawn::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (SmokeTest) SmokeTest->Stop();
    ++SelectionGeneration;
    CancelSelectionPreparation();
    if (PrefetchHandle) PrefetchHandle->ReleaseHandle();
    PrefetchHandle.Reset();
    ReleaseSelection();
    if (ViewerUI) ViewerUI->RemoveFromParent();
    ViewerUI = nullptr;
    Super::EndPlay(EndPlayReason);
}

namespace
{
    void AddViewerOutfitAssets(const FViewerOutfit& Outfit, TArray<FSoftObjectPath>& Assets)
    {
        Assets.AddUnique(Outfit.Environment.IsNull()
            ? FSoftObjectPath(TEXT("/Game/Viewer/Environment/DA_Environment.DA_Environment"))
            : Outfit.Environment.ToSoftObjectPath());
        for (const FViewerPart& Part : Outfit.Parts)
        {
            if (!Part.Mesh.IsNull()) Assets.AddUnique(Part.Mesh.ToSoftObjectPath());
            if (!Part.Animation.IsNull()) Assets.AddUnique(Part.Animation.ToSoftObjectPath());
            if (!Part.Dynamics.IsNull()) Assets.AddUnique(Part.Dynamics.ToSoftObjectPath());
            for (const auto& Material : Part.Materials)
                if (!Material.IsNull()) Assets.AddUnique(Material.ToSoftObjectPath());
        }
    }

    void AddViewerEnvironmentAssets(const UViewerEnvironment& Stage, TArray<FSoftObjectPath>& Assets)
    {
        if (!Stage.Cubemap.IsNull()) Assets.AddUnique(Stage.Cubemap.ToSoftObjectPath());
        for (const auto& Mesh : Stage.Meshes)
        {
            if (!Mesh.Mesh.IsNull()) Assets.AddUnique(Mesh.Mesh.ToSoftObjectPath());
            for (const auto& Material : Mesh.Materials)
                if (!Material.IsNull()) Assets.AddUnique(Material.ToSoftObjectPath());
        }
    }
}

void AViewerPawn::CancelSelectionPreparation()
{
    if (LoadingHandle) LoadingHandle->CancelHandle();
    LoadingHandle.Reset();
    for (USkeletalMeshComponent* Part : PreparingParts) if (Part) Part->DestroyComponent();
    PreparingParts.Reset();
    for (UStaticMeshComponent* Mesh : PreparingStageMeshes) if (Mesh) Mesh->DestroyComponent();
    PreparingStageMeshes.Reset();
    PreparingMaterials.Reset();
    PreparingTextures.Reset();
    PreparingEnvironment = nullptr;
    bPreparingSelection = bEnvironmentDependenciesRequested = bPreparationTexturesRequested = false;
    bPreparationComponentsBuilt = bPreparationFenceStarted = false;
    PreparingRoot->SetRelativeTransform(FTransform::Identity);
}

void AViewerPawn::LoadSelection()
{
    const int32 Generation = ++SelectionGeneration;
    CancelSelectionPreparation();
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit)
    {
        Status = FText::FromString(TEXT("尚未导入人物资源。请先运行编辑器导入脚本。"));
        if (ViewerUI) ViewerUI->Refresh();
        return;
    }
    // The previous scene and character remain visible until the replacement
    // has passed shader, texture, PSO and render-resource readiness checks.
    bPreparingSelection = true;
    bLastSelectionReadyBeforeVisible = false;
    PreparationStartedSeconds = FPlatformTime::Seconds();
    Status = FText::FromString(TEXT("正在载入角色与场景…"));
    if (ViewerUI) ViewerUI->Refresh();
    TArray<FSoftObjectPath> Assets;
    AddViewerOutfitAssets(*Outfit, Assets);
    LoadingHandle = UAssetManager::GetStreamableManager().RequestAsyncLoad(Assets,
        FStreamableDelegate::CreateUObject(this, &AViewerPawn::FinishSelectionLoad, Generation));
    if (PrefetchHandle) PrefetchHandle->ReleaseHandle();
    PrefetchHandle.Reset();
    if (!LoadingHandle)
    {
        CancelSelectionPreparation();
        Status = FText::FromString(TEXT("无法开始载入，当前角色已保留。"));
        if (ViewerUI) ViewerUI->Refresh();
    }
}

void AViewerPawn::FinishSelectionLoad(int32 Generation)
{
    if (Generation != SelectionGeneration || !bPreparingSelection) return;
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit) return;
    if (!bEnvironmentDependenciesRequested)
    {
        PreparingEnvironment = Outfit->Environment.IsNull()
            ? LoadObject<UViewerEnvironment>(nullptr, TEXT("/Game/Viewer/Environment/DA_Environment.DA_Environment"))
            : Outfit->Environment.Get();
        if (!PreparingEnvironment)
        {
            CancelSelectionPreparation();
            Status = FText::FromString(TEXT("无法完整载入场景，当前角色已保留。"));
            if (ViewerUI) ViewerUI->Refresh();
            return;
        }
        bEnvironmentDependenciesRequested = true;
        TArray<FSoftObjectPath> Assets;
        AddViewerOutfitAssets(*Outfit, Assets);
        AddViewerEnvironmentAssets(*PreparingEnvironment, Assets);
        LoadingHandle = UAssetManager::GetStreamableManager().RequestAsyncLoad(Assets,
            FStreamableDelegate::CreateUObject(this, &AViewerPawn::FinishSelectionLoad, Generation));
        return;
    }
    auto AddMaterial = [this](UMaterialInterface* Material)
    {
        if (!Material) return;
        PreparingMaterials.AddUnique(Material);
        // PostLoad schedules the material's render shaders. Explicitly calling
        // CacheShaders here can replace a shared stage material's resources
        // while the previous character is still drawing with them.
    };
    bool Valid = true;
    for (const FViewerPart& Part : Outfit->Parts)
    {
        Valid &= Part.Mesh.Get() && (Part.Animation.IsNull() || Part.Animation.Get());
        for (const auto& Material : Part.Materials)
        {
            Valid &= Material.Get() != nullptr;
            AddMaterial(Material.Get());
        }
    }
    for (const auto& Mesh : PreparingEnvironment->Meshes)
    {
        Valid &= Mesh.Mesh.Get() != nullptr;
        for (const auto& Material : Mesh.Materials)
        {
            Valid &= Material.Get() != nullptr;
            AddMaterial(Material.Get());
        }
    }
    Valid &= PreparingEnvironment->Cubemap.Get() != nullptr;
    if (!Valid)
    {
        CancelSelectionPreparation();
        Status = FText::FromString(TEXT("资源未能完整载入，当前角色已保留。"));
        if (ViewerUI) ViewerUI->Refresh();
    }
}

void AViewerPawn::BuildPreparingComponents()
{
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit || !PreparingEnvironment) return;
    for (const FViewerPart& Part : Outfit->Parts)
    {
        UViewerSkeletalMeshComponent* Component = NewObject<UViewerSkeletalMeshComponent>(this);
        Component->SetupAttachment(PreparingRoot);
        Component->SetRelativeTransform(Part.RelativeTransform);
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetVisibility(false, true);
        Component->SetSkeletalMesh(Part.Mesh.Get());
        Component->SetAnimationMode(EAnimationMode::AnimationSingleNode);
        Component->SetCastShadow(true);
        // Bind every material before registration; never register a visible
        // mesh with the importer's temporary/default material slots.
        for (int32 Slot = 0; Slot < Part.Materials.Num(); ++Slot)
            Component->SetMaterial(Slot, Part.Materials[Slot].Get());
        Component->RegisterComponent();
        Component->SetDynamicsProfile(Part.Dynamics.Get());
        Component->SetSecondaryMotionEnabled(Preferences && Preferences->bSecondaryMotion);
        Component->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        if (Part.Animation.Get())
        {
            Component->PlayAnimation(Part.Animation.Get(), Part.bAnimationLoop);
            Component->TickAnimation(0.f, false);
            Component->RefreshBoneTransforms();
        }
        Component->PrestreamTextures(180.f, true);
        Component->PrecachePSOs();
        Component->UpdateBounds();
        PreparingParts.Add(Component);
    }
    for (const auto& Spec : PreparingEnvironment->Meshes)
    {
        UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(this);
        Component->SetupAttachment(GetRootComponent());
        Component->SetVisibility(false, true);
        Component->SetStaticMesh(Spec.Mesh.Get());
        Component->SetRelativeTransform(Spec.Transform);
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetCastShadow(Spec.bCastShadows);
        for (int32 Index = 0; Index < Spec.Materials.Num(); ++Index)
            Component->SetMaterial(Index, Spec.Materials[Index].Get());
        Component->RegisterComponent();
        Component->PrestreamTextures(180.f, false);
        Component->PrecachePSOs();
        PreparingStageMeshes.Add(Component);
    }
    bPreparationComponentsBuilt = true;
}

void AViewerPawn::TickSelectionPreparation()
{
    if (!bPreparingSelection || !LoadingHandle) return;
    if (FPlatformTime::Seconds() - PreparationStartedSeconds > 120.0)
    {
        CancelSelectionPreparation();
        Status = FText::FromString(TEXT("无法完成资源准备，当前角色已保留。"));
        if (ViewerUI) ViewerUI->Refresh();
        return;
    }
    if (!LoadingHandle->HasLoadCompleted() || !bEnvironmentDependenciesRequested || !PreparingEnvironment
        || PreparingMaterials.IsEmpty()) return;
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit) return;
    for (const FViewerPart& Part : Outfit->Parts)
        if (!Part.Mesh.Get() || Part.Mesh.Get()->IsCompiling() || Part.Mesh.Get()->HasPendingInitOrStreaming()) return;
    for (const auto& Spec : PreparingEnvironment->Meshes)
        if (!Spec.Mesh.Get() || Spec.Mesh.Get()->IsCompiling() || Spec.Mesh.Get()->HasPendingInitOrStreaming()) return;
    for (UMaterialInterface* Material : PreparingMaterials)
        if (!Material || Material->IsCompiling() || !Material->IsComplete()) return;
    if (!bPreparationTexturesRequested)
    {
        for (UMaterialInterface* Material : PreparingMaterials)
        {
            TArray<UTexture*> UsedTextures;
            Material->GetUsedTextures(UsedTextures);
            for (UTexture* Texture : UsedTextures) PreparingTextures.AddUnique(Texture);
        }
        PreparingTextures.AddUnique(PreparingEnvironment->Cubemap.Get());
        for (UTexture* Texture : PreparingTextures)
            if (Texture) Texture->SetForceMipLevelsToBeResident(180.f);
        bPreparationTexturesRequested = true;
    }
    if (!bPreparationComponentsBuilt) BuildPreparingComponents();
    for (UTexture* Texture : PreparingTextures)
        if (!Texture || Texture->IsCompiling() || !Texture->GetResource() || Texture->HasPendingInitOrStreaming()
            || (Texture->IsStreamable() && !Texture->IsFullyStreamedIn())) return;
    for (USkeletalMeshComponent* Part : PreparingParts) if (Part->IsPSOPrecaching()) return;
    for (UStaticMeshComponent* Mesh : PreparingStageMeshes) if (Mesh->IsPSOPrecaching()) return;
    if (!bPreparationFenceStarted)
    {
        PreparationFence.BeginFence(FRenderCommandFence::ESyncDepth::RHIThread);
        bPreparationFenceStarted = true;
        return;
    }
    if (!PreparationFence.IsFenceComplete()) return;
    CommitPreparedSelection();
}

void AViewerPawn::CommitPreparedSelection()
{
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit) return;
    LastSelectionPreparationSeconds = FPlatformTime::Seconds() - PreparationStartedSeconds;
    ReleaseSelection();
    ClearStage();
    Environment = PreparingEnvironment;
    StageMeshes = MoveTemp(PreparingStageMeshes);
    CreateStage(true);
    ApplyPreferences();
    TObjectPtr<USceneComponent> OldRoot = ModelRoot;
    ModelRoot = PreparingRoot;
    PreparingRoot = OldRoot;
    PreparingRoot->SetRelativeTransform(FTransform::Identity);
    Parts = MoveTemp(PreparingParts);
    ActiveHandle = MoveTemp(LoadingHandle);
    FBox Bounds(ForceInit);
    for (int32 Index = 0; Index < Parts.Num(); ++Index)
    {
        USkeletalMeshComponent* Component = Parts[Index];
        Component->SetVisibility(Outfit->Parts[Index].bVisibleByDefault, true);
        Component->PrestreamTextures(3.f, true);
        Component->UpdateBounds();
        if (Outfit->Parts[Index].bVisibleByDefault) Bounds += Component->Bounds.GetBox();
    }
    for (UStaticMeshComponent* Mesh : StageMeshes) { Mesh->SetVisibility(true, true); Mesh->PrestreamTextures(3.f, false); }
    PreparingMaterials.Reset();
    PreparingTextures.Reset();
    PreparingEnvironment = nullptr;
    bPreparingSelection = false;
    bLastSelectionReadyBeforeVisible = true;
    UE_LOG(LogTemp, Display, TEXT("Viewer prepared selection published: %s, %.3fs, %d character parts, %d scene meshes; materials/textures/PSOs ready before visibility"),
        *Outfit->Id.ToString(), LastSelectionPreparationSeconds, Parts.Num(), StageMeshes.Num());
    if (Bounds.IsValid)
    {
        ModelHeight = FMath::Max(50.f, Bounds.GetSize().Z);
        ModelWidth = FMath::Max(20.f, FMath::Max(Bounds.GetSize().X, Bounds.GetSize().Y));
        const FVector Center = Bounds.GetCenter();
        ModelRoot->SetRelativeLocation(Environment && Environment->bPreserveModelOrigin
            ? FVector::ZeroVector : FVector(-Center.X, -Center.Y, -Bounds.Min.Z));
        Focus = FVector(0.f, 0.f, ModelHeight * 0.72f);
        if (Environment && Environment->bFrameFromHeadBone)
        {
            // Keep the source pose and its relationship to the stage. Move
            // the view to the face instead of moving the model or throne.
            for (USkeletalMeshComponent* Part : Parts)
            {
                if (Part && Part->GetBoneIndex(TEXT("b_Head")) != INDEX_NONE)
                {
                    Focus = Part->GetBoneLocation(TEXT("b_Head")) - FVector(0.f, 0.f, 17.f);
                    ModelHeight = 180.f;
                    ModelWidth = 90.f;
                    break;
                }
            }
        }
        DefaultDistance = Environment && Environment->bUseSourceCamera ? Environment->CameraDistance
            : (Environment ? Environment->CameraDistance : 350.f) * ModelHeight / 180.f;
        LastViewportSize = FVector2D::ZeroVector;
    }
    const bool bHasAnimation = Outfit->Parts.ContainsByPredicate([](const FViewerPart& Part) { return !Part.Animation.IsNull(); });
    Status = FText::FromString(bHasAnimation ? TEXT("待机动作 · 可分部件查看") : TEXT("原始绑定姿势 · 可分部件查看"));
    ResetView();
    if (ViewerUI) ViewerUI->Refresh();
    PrefetchAdjacentCharacter();
}


void AViewerPawn::PrefetchAdjacentCharacter()
{
    if (!Catalog || Catalog->Characters.Num() < 2) return;
    const int32 Next = (CharacterIndex + 1) % Catalog->Characters.Num();
    if (Catalog->Characters[Next].Outfits.IsEmpty()) return;
    const int32 Generation = SelectionGeneration;
    TArray<FSoftObjectPath> Assets;
    AddViewerOutfitAssets(Catalog->Characters[Next].Outfits[0], Assets);
    PrefetchHandle = UAssetManager::GetStreamableManager().RequestAsyncLoad(Assets,
        FStreamableDelegate::CreateWeakLambda(this, [this, Generation, Next, Assets]() mutable
        {
            if (Generation != SelectionGeneration || !Catalog || bPreparingSelection) return;
            const auto& Outfit = Catalog->Characters[Next].Outfits[0];
            UViewerEnvironment* Stage = Outfit.Environment.IsNull()
                ? LoadObject<UViewerEnvironment>(nullptr, TEXT("/Game/Viewer/Environment/DA_Environment.DA_Environment"))
                : Outfit.Environment.Get();
            if (Stage) AddViewerEnvironmentAssets(*Stage, Assets);
            PrefetchHandle = UAssetManager::GetStreamableManager().RequestAsyncLoad(Assets);
        }));
}

void AViewerPawn::ChangeCharacter(int32 Direction)
{
    if (!Catalog || Catalog->Characters.Num() < 2) return;
    SelectCharacter((CharacterIndex + Direction + Catalog->Characters.Num()) % Catalog->Characters.Num());
}

void AViewerPawn::ChangeOutfit(int32 Direction)
{
    if (!HasMultipleOutfits()) return;
    const int32 Count = Catalog->Characters[CharacterIndex].Outfits.Num();
    SelectOutfit((OutfitIndex + Direction + Count) % Count);
}

void AViewerPawn::SelectCharacter(int32 Index)
{
    if (!Catalog || !Catalog->Characters.IsValidIndex(Index) || CharacterIndex == Index) return;
    CharacterIndex = Index;
    OutfitIndex = 0;
    LoadSelection();
}

void AViewerPawn::SelectOutfit(int32 Index)
{
    if (!Catalog || !Catalog->Characters.IsValidIndex(CharacterIndex) || OutfitIndex == Index ||
        !Catalog->Characters[CharacterIndex].Outfits.IsValidIndex(Index)) return;
    OutfitIndex = Index;
    LoadSelection();
}

TArray<FString> AViewerPawn::GetCharacterOptions() const
{
    TArray<FString> Options;
    if (!Catalog) return Options;
    for (int32 Index = 0; Index < Catalog->Characters.Num(); ++Index)
        Options.Add(FString::Printf(TEXT("%02d  %s"), Index + 1, *Catalog->Characters[Index].DisplayName.ToString()));
    return Options;
}

TArray<FString> AViewerPawn::GetOutfitOptions() const
{
    TArray<FString> Options;
    if (!Catalog || !Catalog->Characters.IsValidIndex(CharacterIndex)) return Options;
    const auto& Outfits = Catalog->Characters[CharacterIndex].Outfits;
    for (int32 Index = 0; Index < Outfits.Num(); ++Index)
        Options.Add(FString::Printf(TEXT("%02d  %s"), Index + 1, *Outfits[Index].DisplayName.ToString()));
    return Options;
}

void AViewerPawn::ResetView()
{
    Yaw = 0.f;
    DisplayYaw = 0.f;
    ModelRoot->SetRelativeRotation(FRotator::ZeroRotator);
    Pitch = Environment ? Environment->CameraPitch : 4.4f;
    Distance = DefaultDistance;
    bPointerDragging = false;
    bPinching = false;
    for (USkeletalMeshComponent* Part : Parts)
        if (UViewerSkeletalMeshComponent* Dynamic = Cast<UViewerSkeletalMeshComponent>(Part)) Dynamic->ResetSecondaryMotion();
    UpdateCamera();
}

void AViewerPawn::SetPartVisible(int32 Index, bool bVisible)
{
    if (bPreparingSelection) return;
    if (Parts.IsValidIndex(Index) && Parts[Index]) Parts[Index]->SetVisibility(bVisible, true);
}

bool AViewerPawn::IsPartVisible(int32 Index) const
{
    return Parts.IsValidIndex(Index) && Parts[Index] && Parts[Index]->IsVisible();
}

bool AViewerPawn::IsPartLoaded(int32 Index) const
{
    return Parts.IsValidIndex(Index) && Parts[Index] != nullptr;
}

void AViewerPawn::Orbit(FVector2D Delta)
{
    Yaw = FMath::UnwindDegrees(Yaw - Delta.X * 0.22f);
    Pitch = FMath::Clamp(Pitch + Delta.Y * 0.18f, -30.f, 65.f);
}

void AViewerPawn::Zoom(float Amount)
{
    if (ViewerUI)
    {
        if (APlayerController* PC = Cast<APlayerController>(GetController()))
        {
            float X = 0.f, Y = 0.f;
            if (PC->GetMousePosition(X, Y) && ViewerUI->BlocksModelInput(FVector2D(X, Y))) return;
        }
    }
    Distance = FMath::Clamp(Distance * FMath::Exp(-Amount * 0.12f), ModelHeight * 0.6f, DefaultDistance * 2.2f);
}

void AViewerPawn::ReadPointerInput()
{
    APlayerController* PC = Cast<APlayerController>(GetController());
    if (!PC) return;
    float X1 = 0.f, Y1 = 0.f, X2 = 0.f, Y2 = 0.f;
    bool First = false, Second = false;
    PC->GetInputTouchState(ETouchIndex::Touch1, X1, Y1, First);
    PC->GetInputTouchState(ETouchIndex::Touch2, X2, Y2, Second);
    const FVector2D Point1(X1, Y1), Point2(X2, Y2);
    if (First && Second)
    {
        if (ViewerUI && (ViewerUI->BlocksModelInput(Point1) || ViewerUI->BlocksModelInput(Point2)))
        {
            bPinching = bPointerDragging = false;
            return;
        }
        const float Separation = FVector2D::Distance(Point1, Point2);
        if (bPinching && PreviousPinch > 1.f && Separation > 1.f)
            Distance = FMath::Clamp(Distance * PreviousPinch / Separation, ModelHeight * 0.6f, DefaultDistance * 2.2f);
        PreviousPinch = Separation;
        bPinching = true;
        bPointerDragging = false;
        return;
    }
    if (bPinching) { bPinching = false; bPointerDragging = false; }
    FVector2D Pointer = Point1;
    bool Pressed = First;
    if (!First)
    {
        float MouseX = 0.f, MouseY = 0.f;
        Pressed = PC->GetMousePosition(MouseX, MouseY) && PC->IsInputKeyDown(EKeys::LeftMouseButton);
        Pointer = FVector2D(MouseX, MouseY);
    }
    if (!Pressed || (ViewerUI && ViewerUI->BlocksModelInput(Pointer)))
    {
        bPointerDragging = false;
        return;
    }
    if (bPointerDragging) Orbit(Pointer - PreviousPointer);
    PreviousPointer = Pointer;
    bPointerDragging = true;
}

void AViewerPawn::UpdateCamera()
{
    int32 Width = 0, Height = 0;
    if (const APlayerController* PC = Cast<APlayerController>(GetController())) PC->GetViewportSize(Width, Height);
    if (Width > 0 && Height > 0)
    {
        const float Aspect = static_cast<float>(Width) / Height;
        const float ReferenceFOV = Environment ? Environment->CameraHorizontalFOV : 29.9281126f;
        const float ReferenceAspect = Environment ? Environment->ReferenceAspectRatio : 1.7777778f;
        const float HalfVerticalTangent = FMath::Tan(FMath::DegreesToRadians(ReferenceFOV * 0.5f)) / ReferenceAspect;
        const float HalfHorizontalTangent = HalfVerticalTangent * Aspect;
        Camera->FieldOfView = FMath::RadiansToDegrees(2.f * FMath::Atan(HalfHorizontalTangent));
        Camera->AspectRatio = Aspect;
        const FVector2D Size(Width, Height);
        if (!Size.Equals(LastViewportSize))
        {
            const float ZoomRatio = DefaultDistance > 1.f ? Distance / DefaultDistance : 1.f;
            const float ReferenceDistance = Environment && Environment->bUseSourceCamera ? Environment->CameraDistance
                : (Environment ? Environment->CameraDistance : 350.f) * ModelHeight / 180.f;
            DefaultDistance = Environment && Environment->bUseSourceCamera ? ReferenceDistance
                : FMath::Max(ReferenceDistance, ModelWidth / FMath::Max(0.01f, 1.4f * HalfHorizontalTangent));
            Distance = DefaultDistance * ZoomRatio;
            LastViewportSize = Size;
            UE_LOG(LogTemp, Display, TEXT("Viewer viewport changed: %dx%d, aspect %.4f, horizontal FOV %.3f, unconstrained"), Width, Height, Aspect, Camera->FieldOfView);
        }
    }
    if (Environment && Environment->bUseSourceCamera)
    {
        const FTransform& Source = Environment->SourceCameraTransform;
        FRotator Rotation = Source.Rotator();
        Rotation.Pitch += Pitch - Environment->CameraPitch;
        const FVector ViewTarget = Source.GetLocation() + Source.GetRotation().GetForwardVector() * Environment->CameraDistance;
        Camera->SetWorldLocationAndRotation(ViewTarget - Rotation.Vector() * Distance, Rotation);
        if (StagePostProcess && Preferences && Preferences->bDepthOfField)
            StagePostProcess->Settings.DepthOfFieldFocalDistance = Distance;
        return;
    }
    const float CameraYaw = Environment ? Environment->CameraYaw : -90.f;
    const FRotator Rotation(Pitch, CameraYaw, 0.f);
    const float HalfWidth = Distance * FMath::Tan(FMath::DegreesToRadians(Camera->FieldOfView * 0.5f));
    const FVector ViewTarget = Focus - FRotationMatrix(Rotation).GetUnitAxis(EAxis::Y) * HalfWidth * 0.18f;
    const FVector Position = ViewTarget - Rotation.Vector() * Distance;
    Camera->SetWorldLocation(Position);
    Camera->SetWorldRotation(Rotation);
    if (StagePostProcess && Preferences && Preferences->bDepthOfField)
        StagePostProcess->Settings.DepthOfFieldFocalDistance = Distance;
}

FText AViewerPawn::GetCharacterLabel() const
{
    return Catalog && Catalog->Characters.IsValidIndex(CharacterIndex)
        ? Catalog->Characters[CharacterIndex].DisplayName : FText::FromString(TEXT("人物尚未导入"));
}
FText AViewerPawn::GetOutfitLabel() const
{
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    return Outfit ? Outfit->DisplayName : FText::FromString(TEXT("暂无服装"));
}
FText AViewerPawn::GetStatusLabel() const { return Status; }
bool AViewerPawn::HasMultipleCharacters() const { return Catalog && Catalog->Characters.Num() > 1; }
bool AViewerPawn::HasMultipleOutfits() const
{
    return Catalog && Catalog->Characters.IsValidIndex(CharacterIndex) && Catalog->Characters[CharacterIndex].Outfits.Num() > 1;
}

int32 AViewerPawn::GetCharacterCount() const { return Catalog ? Catalog->Characters.Num() : 0; }

FText AViewerPawn::GetCharacterDisplayName(int32 Index) const
{
    return Catalog && Catalog->Characters.IsValidIndex(Index) ? Catalog->Characters[Index].DisplayName : FText::GetEmpty();
}

UTexture2D* AViewerPawn::GetCharacterPortrait(int32 Index) const
{
    return Catalog && Catalog->Characters.IsValidIndex(Index)
        ? Catalog->Characters[Index].Portrait.LoadSynchronous() : nullptr;
}

void AViewerPawn::SetPreferenceChoice(EViewerSetting Setting, int32 Index)
{
    if (!Preferences) return;
    Preferences->SetChoice(Setting, Index);
    ApplyPreferences();
    Preferences->Persist();
    if (ViewerUI) ViewerUI->Refresh();
}

void AViewerPawn::SetPreferenceToggle(EViewerSetting Setting, bool bEnabled)
{
    if (!Preferences) return;
    Preferences->SetToggle(Setting, bEnabled);
    ApplyPreferences();
    Preferences->Persist();
    if (ViewerUI) ViewerUI->Refresh();
}

void AViewerPawn::ResetPreferences()
{
    if (!Preferences) return;
    Preferences->ResetDefaults();
    ApplyPreferences();
    Preferences->Persist();
    if (ViewerUI) ViewerUI->Refresh();
}

void AViewerPawn::ReplayAnimation()
{
    if (bPreparingSelection) return;
    const FViewerOutfit* Outfit = GetCurrentOutfit();
    if (!Outfit) return;
    for (int32 Index = 0; Index < Parts.Num(); ++Index)
    {
        USkeletalMeshComponent* Component = Parts[Index];
        if (!Component || !Outfit->Parts.IsValidIndex(Index)) continue;
        if (UAnimSequence* Animation = Outfit->Parts[Index].Animation.Get())
            Component->PlayAnimation(Animation, Outfit->Parts[Index].bAnimationLoop);
        if (UViewerSkeletalMeshComponent* Dynamic = Cast<UViewerSkeletalMeshComponent>(Component))
            Dynamic->ResetSecondaryMotion();
    }
}

void AViewerPawn::ApplyPreferences()
{
    if (!Preferences) return;
    Preferences->ApplyEngineSettings();
    if (StagePostProcess)
    {
        FPostProcessSettings& Post = StagePostProcess->Settings;
        if (Environment) Post = Environment->PostProcess;
        Post.bOverride_AutoExposureMethod = true;
        Post.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
        Post.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
        Post.AutoExposureApplyPhysicalCameraExposure = false;
        Post.bOverride_BloomIntensity = true;
        Post.BloomIntensity = Preferences->bPostProcessing && Preferences->bBloom
            ? Environment ? Environment->PostProcess.BloomIntensity : 0.15f : 0.f;
        Post.bOverride_VignetteIntensity = true;
        Post.VignetteIntensity = Preferences->bPostProcessing && Preferences->bVignette
            ? Environment ? Environment->PostProcess.VignetteIntensity : 0.25f : 0.f;
        Post.bOverride_DepthOfFieldScale = true;
        Post.DepthOfFieldScale = Preferences->bPostProcessing && Preferences->bDepthOfField ? 1.f : 0.f;
        Post.bOverride_DepthOfFieldFstop = true;
        Post.DepthOfFieldFstop = 2.8f;
        Post.bOverride_DepthOfFieldFocalDistance = true;
        Post.DepthOfFieldFocalDistance = Distance;
        if (!Preferences->bPostProcessing)
        {
            Post.bOverride_ColorContrast = true;
            Post.ColorContrast = FVector4(1.f, 1.f, 1.f, 1.f);
            Post.bOverride_ColorSaturation = true;
            Post.ColorSaturation = FVector4(1.f, 1.f, 1.f, 1.f);
        }
    }
    for (ULightComponent* Light : StageLights)
    {
        if (!Light) continue;
        const FViewerEnvironmentLight* Source = Environment ? Environment->Lights.FindByPredicate(
            [Light](const FViewerEnvironmentLight& Spec) { return Spec.Id == Light->GetFName(); }) : nullptr;
        Light->SetCastShadows(Preferences->Shadows > 0 && (!Source || Source->bCastShadows));
    }
    for (USkeletalMeshComponent* Part : Parts)
        if (UViewerSkeletalMeshComponent* Dynamic = Cast<UViewerSkeletalMeshComponent>(Part))
            Dynamic->SetSecondaryMotionEnabled(Preferences->bSecondaryMotion);
}

void AViewerPawn::ClearStage()
{
    for (UStaticMeshComponent* Mesh : StageMeshes) if (Mesh) Mesh->DestroyComponent();
    StageMeshes.Reset();
    for (ULightComponent* Light : StageLights) if (Light) Light->DestroyComponent();
    StageLights.Reset();
    if (StageSky) StageSky->DestroyComponent();
    StageSky = nullptr;
    if (StagePostProcess) StagePostProcess->DestroyComponent();
    StagePostProcess = nullptr;
}

void AViewerPawn::LoadOutfitEnvironment(const FViewerOutfit& Outfit)
{
    UViewerEnvironment* Requested = Outfit.Environment.IsNull()
        ? LoadObject<UViewerEnvironment>(nullptr, TEXT("/Game/Viewer/Environment/DA_Environment.DA_Environment"))
        : Outfit.Environment.LoadSynchronous();
    if (!Requested)
    {
        UE_LOG(LogTemp, Error, TEXT("Role environment unavailable for outfit %s: %s"), *Outfit.Id.ToString(), *Outfit.Environment.ToString());
        return;
    }
    if (Requested == Environment) return;
    ClearStage();
    Environment = Requested;
    CreateStage();
    ApplyPreferences();
    LastViewportSize = FVector2D::ZeroVector;
}

void AViewerPawn::CreateStage(bool bUsePreparedMeshes)
{
    USceneComponent* Root = GetRootComponent();
    if (Environment)
    {
        if (!bUsePreparedMeshes) for (const FViewerEnvironmentMesh& Spec : Environment->Meshes)
        {
            UStaticMesh* Mesh = Spec.Mesh.LoadSynchronous();
            if (!Mesh)
            {
                UE_LOG(LogTemp, Error, TEXT("Role environment mesh failed to load: %s"), *Spec.Mesh.ToString());
                continue;
            }
            UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(this, Spec.Id);
            Component->SetupAttachment(Root);
            Component->SetStaticMesh(Mesh);
            Component->SetRelativeTransform(Spec.Transform);
            Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            Component->SetCastShadow(Spec.bCastShadows);
            for (int32 Index = 0; Index < Spec.Materials.Num(); ++Index)
                if (UMaterialInterface* Material = Spec.Materials[Index].LoadSynchronous()) Component->SetMaterial(Index, Material);
            Component->RegisterComponent();
            StageMeshes.Add(Component);
        }
        USkyLightComponent* Sky = NewObject<USkyLightComponent>(this, TEXT("RoleSkyLight"));
        StageSky = Sky;
        Sky->SetupAttachment(Root);
        Sky->SetMobility(EComponentMobility::Movable);
        Sky->SourceType = SLS_SpecifiedCubemap;
        Sky->SetCubemap(Environment->Cubemap.LoadSynchronous());
        Sky->SetIntensity(Environment->SkyIntensity);
        Sky->SetLightColor(Environment->SkyColor);
        Sky->SetCastShadows(false);
        Sky->RegisterComponent();
        UE_LOG(LogTemp, Display, TEXT("Role environment: %s; meshes=%d; HDR=%s; source lights=%d"),
            *Environment->GetPathName(), StageMeshes.Num(), *GetPathNameSafe(Sky->Cubemap), Environment->Lights.Num());
        for (const FViewerEnvironmentLight& Spec : Environment->Lights)
        {
            ULightComponent* Light = Spec.Kind == TEXT("Directional")
                ? static_cast<ULightComponent*>(NewObject<UDirectionalLightComponent>(this, Spec.Id))
                : Spec.Kind == TEXT("Point") ? static_cast<ULightComponent*>(NewObject<UPointLightComponent>(this, Spec.Id))
                : static_cast<ULightComponent*>(NewObject<USpotLightComponent>(this, Spec.Id));
            Light->SetupAttachment(Root);
            Light->SetMobility(EComponentMobility::Movable);
            Light->SetRelativeLocationAndRotation(Spec.Location, Spec.Rotation);
            if (UPointLightComponent* Local = Cast<UPointLightComponent>(Light))
            {
                Local->SetIntensityUnits(static_cast<ELightUnits>(Spec.IntensityUnits));
                Local->SetAttenuationRadius(Spec.Radius);
                Local->SetUseInverseSquaredFalloff(Spec.bInverseSquaredFalloff);
                Local->SetLightFalloffExponent(Spec.FalloffExponent);
                Local->SetSourceRadius(Spec.SourceRadius);
                Local->SetSourceLength(Spec.SourceLength);
            }
            if (USpotLightComponent* Spot = Cast<USpotLightComponent>(Light))
            {
                Spot->SetInnerConeAngle(Spec.InnerCone);
                Spot->SetOuterConeAngle(Spec.OuterCone);
            }
            Light->SetIntensity(Spec.Intensity);
            Light->SetLightColor(Spec.Color);
            Light->SetTemperature(Spec.Temperature);
            Light->bUseTemperature = Spec.bUseTemperature;
            Light->SetSpecularScale(Spec.SpecularScale);
            Light->SetShadowBias(Spec.ShadowBias);
            Light->SetCastShadows(Spec.bCastShadows);
            Light->RegisterComponent();
            StageLights.Add(Light);
        }
        StagePostProcess = NewObject<UPostProcessComponent>(this);
        StagePostProcess->SetupAttachment(Root);
        StagePostProcess->bUnbound = true;
        StagePostProcess->Settings = Environment->PostProcess;
        StagePostProcess->RegisterComponent();
        return;
    }
    UStaticMeshComponent* Plinth = NewObject<UStaticMeshComponent>(this);
    Plinth->SetupAttachment(Root);
    Plinth->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder")));
    Plinth->SetRelativeLocation(FVector(0.f, 0.f, -4.f));
    Plinth->SetRelativeScale3D(FVector(1.65f, 1.65f, 0.08f));
    Plinth->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    if (UMaterialInterface* Stage = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Viewer/Materials/M_Stage.M_Stage")))
        Plinth->SetMaterial(0, Stage);
    Plinth->RegisterComponent();

    UDirectionalLightComponent* Key = NewObject<UDirectionalLightComponent>(this);
    Key->SetupAttachment(Root);
    Key->SetMobility(EComponentMobility::Movable);
    Key->SetRelativeRotation(FRotator(-38.f, -32.f, 0.f));
    Key->SetIntensity(3.5f);
    Key->SetLightColor(FLinearColor(1.f, 0.94f, 0.86f));
    Key->DynamicShadowDistanceMovableLight = 1000.f;
    Key->DynamicShadowCascades = 2;
    Key->RegisterComponent();
    StageLights.Add(Key);

    auto AddFill = [this, Root](FVector Location, FLinearColor Color, float Intensity)
    {
        UPointLightComponent* Light = NewObject<UPointLightComponent>(this);
        Light->SetupAttachment(Root);
        Light->SetMobility(EComponentMobility::Movable);
        Light->SetRelativeLocation(Location);
        Light->SetIntensity(Intensity);
        Light->SetAttenuationRadius(900.f);
        Light->SetLightColor(Color);
        Light->SetCastShadows(false);
        Light->RegisterComponent();
        StageLights.Add(Light);
    };
    AddFill(FVector(160.f, 200.f, 140.f), FLinearColor(0.62f, 0.77f, 1.f), 4200.f);
    AddFill(FVector(-150.f, -150.f, 210.f), FLinearColor(0.68f, 0.88f, 1.f), 6000.f);

    UPostProcessComponent* Post = NewObject<UPostProcessComponent>(this);
    StagePostProcess = Post;
    Post->SetupAttachment(Root);
    Post->bUnbound = true;
    Post->Settings.bOverride_AutoExposureMethod = true;
    Post->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
    Post->Settings.bOverride_AutoExposureBias = true;
    Post->Settings.AutoExposureBias = 1.f;
    Post->Settings.bOverride_MotionBlurAmount = true;
    Post->Settings.MotionBlurAmount = 0.f;
    Post->Settings.bOverride_BloomIntensity = true;
    Post->Settings.BloomIntensity = 0.12f;
    Post->Settings.bOverride_VignetteIntensity = true;
    Post->Settings.VignetteIntensity = 0.22f;
    Post->RegisterComponent();
}
