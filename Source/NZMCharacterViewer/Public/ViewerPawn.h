#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "ViewerCatalog.h"
#include "ViewerPreferences.h"
#include "RenderCommandFence.h"
#include "ViewerPawn.generated.h"

class UCameraComponent;
class USkeletalMeshComponent;
class UViewerWidget;
class UTexture2D;
class UTexture;
class UMaterialInterface;
class UPostProcessComponent;
class ULightComponent;
class USkyLightComponent;
class UStaticMeshComponent;
class UViewerEnvironment;
class UViewerSmokeTestComponent;
struct FStreamableHandle;

UCLASS()
class NZMCHARACTERVIEWER_API AViewerPawn : public APawn
{
    GENERATED_BODY()

public:
    AViewerPawn();
    virtual void Tick(float DeltaSeconds) override;
    virtual void SetupPlayerInputComponent(UInputComponent* Input) override;
    void ChangeCharacter(int32 Direction);
    void ChangeOutfit(int32 Direction);
    void SelectCharacter(int32 Index);
    void SelectOutfit(int32 Index);
    TArray<FString> GetCharacterOptions() const;
    TArray<FString> GetOutfitOptions() const;
    int32 GetCharacterIndex() const { return CharacterIndex; }
    int32 GetOutfitIndex() const { return OutfitIndex; }
    void ResetView();
    void SetPartVisible(int32 Index, bool bVisible);
    bool IsPartVisible(int32 Index) const;
    bool IsPartLoaded(int32 Index) const;
    const FViewerOutfit* GetCurrentOutfit() const;
    FText GetCharacterLabel() const;
    FText GetOutfitLabel() const;
    FText GetStatusLabel() const;
    bool HasMultipleCharacters() const;
    bool HasMultipleOutfits() const;
    int32 GetCharacterCount() const;
    FText GetCharacterDisplayName(int32 Index) const;
    UTexture2D* GetCharacterPortrait(int32 Index) const;
    UViewerPreferences* GetPreferences() const { return Preferences; }
    void SetPreferenceChoice(EViewerSetting Setting, int32 Index);
    void SetPreferenceToggle(EViewerSetting Setting, bool bEnabled);
    void ResetPreferences();
    void ReplayAnimation();

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    friend class UViewerSmokeTestComponent;
    UPROPERTY() TObjectPtr<USceneComponent> ModelRoot;
    UPROPERTY() TObjectPtr<USceneComponent> PreparingRoot;
    UPROPERTY() TObjectPtr<UCameraComponent> Camera;
    UPROPERTY() TObjectPtr<UViewerCatalog> Catalog;
    UPROPERTY() TObjectPtr<UViewerWidget> ViewerUI;
    UPROPERTY() TObjectPtr<UViewerPreferences> Preferences;
    UPROPERTY() TObjectPtr<UViewerEnvironment> Environment;
    UPROPERTY() TObjectPtr<UPostProcessComponent> StagePostProcess;
    UPROPERTY() TArray<TObjectPtr<ULightComponent>> StageLights;
    UPROPERTY() TObjectPtr<USkyLightComponent> StageSky;
    UPROPERTY() TArray<TObjectPtr<UStaticMeshComponent>> StageMeshes;
    UPROPERTY(Transient) TObjectPtr<UViewerSmokeTestComponent> SmokeTest;
    UPROPERTY() TArray<TObjectPtr<USkeletalMeshComponent>> Parts;
    UPROPERTY() TArray<TObjectPtr<USkeletalMeshComponent>> PreparingParts;
    UPROPERTY() TArray<TObjectPtr<UStaticMeshComponent>> PreparingStageMeshes;
    UPROPERTY() TArray<TObjectPtr<UMaterialInterface>> PreparingMaterials;
    UPROPERTY() TArray<TObjectPtr<UTexture>> PreparingTextures;
    UPROPERTY() TObjectPtr<UViewerEnvironment> PreparingEnvironment;
    int32 CharacterIndex = 0;
    int32 OutfitIndex = 0;
    float Yaw = 0.f;
    float DisplayYaw = 0.f;
    float ModelWidth = 90.f;
    float Pitch = 6.f;
    float Distance = 330.f;
    float DefaultDistance = 330.f;
    float ModelHeight = 180.f;
    FVector Focus = FVector(0.f, 0.f, 100.f);
    FVector2D PreviousPointer = FVector2D::ZeroVector;
    FVector2D LastViewportSize = FVector2D::ZeroVector;
    float PreviousPinch = 0.f;
    bool bPointerDragging = false;
    bool bPinching = false;
    FText Status;
    TSharedPtr<FStreamableHandle> LoadingHandle;
    TSharedPtr<FStreamableHandle> ActiveHandle;
    TSharedPtr<FStreamableHandle> PrefetchHandle;
    FRenderCommandFence PreparationFence;
    bool bPreparingSelection = false;
    bool bEnvironmentDependenciesRequested = false;
    bool bPreparationTexturesRequested = false;
    bool bPreparationComponentsBuilt = false;
    bool bPreparationFenceStarted = false;
    bool bLastSelectionReadyBeforeVisible = false;
    double PreparationStartedSeconds = 0.0;
    double LastSelectionPreparationSeconds = 0.0;
    int32 SelectionGeneration = 0;
    void LoadSelection();
    void FinishSelectionLoad(int32 Generation);
    void TickSelectionPreparation();
    void BuildPreparingComponents();
    void CommitPreparedSelection();
    void CancelSelectionPreparation();
    void PrefetchAdjacentCharacter();
    void ReleaseSelection();
    void InitializeInterface();
    void CreateStage(bool bUsePreparedMeshes = false);
    void ClearStage();
    void LoadOutfitEnvironment(const FViewerOutfit& Outfit);
    void UpdateCamera();
    void ReadPointerInput();
    void ApplyPreferences();
    void Orbit(FVector2D Delta);
    void Zoom(float Amount);
};
