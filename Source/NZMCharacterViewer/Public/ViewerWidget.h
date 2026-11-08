#pragma once

#include "CoreMinimal.h"
#include "Blueprint/UserWidget.h"
#include "Components/Button.h"
#include "Components/CheckBox.h"
#include "Components/ComboBoxString.h"
#include "ViewerPreferences.h"
#include "ViewerWidget.generated.h"

class AViewerPawn;
class UBorder;
class UCanvasPanel;
class UCanvasPanelSlot;
class UImage;
class UScrollBox;
class USizeBox;
class UTextBlock;
class UTexture2D;
class UVerticalBox;
class UViewerWidget;

UCLASS()
class NZMCHARACTERVIEWER_API UViewerButton : public UButton
{
    GENERATED_BODY()
public:
    UViewerButton(const FObjectInitializer& ObjectInitializer);
};

UCLASS()
class NZMCHARACTERVIEWER_API UViewerComboBoxString : public UComboBoxString
{
    GENERATED_BODY()
public:
    UViewerComboBoxString(const FObjectInitializer& ObjectInitializer);
};

/** Stable Catalog index and precise touch activation inside the scrolling rail. */
UCLASS()
class NZMCHARACTERVIEWER_API UViewerCharacterTile : public UViewerButton
{
    GENERATED_BODY()
public:
    void Configure(UViewerWidget* InHost, AViewerPawn* InViewer, int32 InIndex);
    void UpdatePortrait();
    void SetSelected(bool bSelected);
    void SetCardSize(float Width, float Height, bool bCompact);
private:
    UPROPERTY() TObjectPtr<UViewerWidget> Host;
    UPROPERTY() TObjectPtr<AViewerPawn> Viewer;
    UPROPERTY() TObjectPtr<USizeBox> CardSize;
    UPROPERTY() TObjectPtr<UImage> Portrait;
    UPROPERTY() TObjectPtr<UTextBlock> NameText;
    UPROPERTY() TObjectPtr<UTextBlock> FallbackText;
    UPROPERTY() TObjectPtr<UTexture2D> LastPortrait;
    int32 Index = INDEX_NONE;
    UFUNCTION() void OnTileClicked();
};

UCLASS()
class NZMCHARACTERVIEWER_API UViewerSettingPicker : public UViewerComboBoxString
{
    GENERATED_BODY()
public:
    void Configure(UViewerWidget* InHost, AViewerPawn* InViewer, EViewerSetting InSetting, const TArray<FString>& Options);
    void Sync(const UViewerPreferences* Preferences);
private:
    UPROPERTY() TObjectPtr<UViewerWidget> Host;
    UPROPERTY() TObjectPtr<AViewerPawn> Viewer;
    EViewerSetting Setting = EViewerSetting::Quality;
    bool bUpdating = false;
    UFUNCTION() void OnChoiceChanged(FString SelectedItem, ESelectInfo::Type SelectionType);
};

UCLASS()
class NZMCHARACTERVIEWER_API UViewerSettingToggle : public UCheckBox
{
    GENERATED_BODY()
public:
    void Configure(UViewerWidget* InHost, AViewerPawn* InViewer, EViewerSetting InSetting, const FText& Label);
    void Sync(const UViewerPreferences* Preferences);
private:
    UPROPERTY() TObjectPtr<UViewerWidget> Host;
    UPROPERTY() TObjectPtr<AViewerPawn> Viewer;
    EViewerSetting Setting = EViewerSetting::Bloom;
    bool bUpdating = false;
    UFUNCTION() void OnToggleChanged(bool bChecked);
};

UCLASS()
class NZMCHARACTERVIEWER_API UViewerPartToggle : public UCheckBox
{
    GENERATED_BODY()
public:
    void Configure(AViewerPawn* InViewer, int32 InIndex, const FText& Label);
private:
    UPROPERTY() TObjectPtr<AViewerPawn> Viewer;
    int32 Index = 0;
    UFUNCTION() void OnChanged(bool bChecked);
};

UCLASS()
class NZMCHARACTERVIEWER_API UViewerWidget : public UUserWidget
{
    GENERATED_BODY()
public:
    void InitializeViewer(AViewerPawn* InViewer);
    void Refresh();
    void SelectCharacterTile(int32 Index);
    bool BlocksModelInput(FVector2D ViewportPixelPosition) const;
    bool ConsumeModalPointer(FVector2D ViewportPixelPosition, bool bPressed);
    // Actual viewport pixels available to the model after safe-area/UI layout.
    FBox2D GetModelViewportRect() const;
protected:
    virtual TSharedRef<SWidget> RebuildWidget() override;
    virtual void NativeConstruct() override;
    virtual void NativeTick(const FGeometry& MyGeometry, float InDeltaTime) override;
private:
    UPROPERTY() TObjectPtr<AViewerPawn> Viewer;
    UPROPERTY() TObjectPtr<UCanvasPanel> LayoutCanvas;
    UPROPERTY() TObjectPtr<UBorder> CharacterRail;
    UPROPERTY() TObjectPtr<UScrollBox> CharacterScroll;
    UPROPERTY() TObjectPtr<UVerticalBox> CharacterList;
    UPROPERTY() TArray<TObjectPtr<UViewerCharacterTile>> CharacterTiles;
    UPROPERTY() TObjectPtr<UVerticalBox> Header;
    UPROPERTY() TObjectPtr<UTextBlock> CharacterLabel;
    UPROPERTY() TObjectPtr<UTextBlock> OutfitTitle;
    UPROPERTY() TObjectPtr<USizeBox> TitleAccentSize;
    UPROPERTY() TObjectPtr<UTextBlock> StatusText;
    UPROPERTY() TObjectPtr<UVerticalBox> ActionBar;
    UPROPERTY() TArray<TObjectPtr<USizeBox>> ActionSizes;
    UPROPERTY() TArray<TObjectPtr<UTextBlock>> ActionLabels;
    UPROPERTY() TObjectPtr<UButton> SettingsButton;
    UPROPERTY() TObjectPtr<UBorder> Footer;
    UPROPERTY() TObjectPtr<UComboBoxString> OutfitPicker;
    UPROPERTY() TObjectPtr<UButton> PreviousOutfit;
    UPROPERTY() TObjectPtr<UButton> NextOutfit;
    UPROPERTY() TObjectPtr<UCanvasPanel> SettingsOverlay;
    UPROPERTY() TObjectPtr<UBorder> SettingsPanel;
    UPROPERTY() TObjectPtr<UScrollBox> SettingsScroll;
    UPROPERTY() TObjectPtr<UVerticalBox> PartList;
    UPROPERTY() TObjectPtr<UVerticalBox> GraphicsSettings;
    UPROPERTY() TObjectPtr<UVerticalBox> EffectsSettings;
    UPROPERTY() TObjectPtr<UVerticalBox> PartsSettings;
    UPROPERTY() TArray<TObjectPtr<UButton>> SettingsTabs;
    UPROPERTY() TArray<TObjectPtr<UViewerSettingPicker>> ChoicePickers;
    UPROPERTY() TArray<TObjectPtr<UViewerSettingToggle>> SettingToggles;
    UPROPERTY() TObjectPtr<UButton> RestoreDefaultsButton;
    UPROPERTY() TObjectPtr<USizeBox> RestoreControl;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> RailSlot;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> HeaderSlot;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> ActionsSlot;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> FooterSlot;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> SettingsSlot;
    UPROPERTY() TObjectPtr<UCanvasPanelSlot> RestoreSlot;
    FVector2D LastLayoutSize = FVector2D::ZeroVector;
    FVector2D LastViewportPixels = FVector2D::ZeroVector;
    TArray<FString> CachedOutfitOptions;
    bool bRefreshing = false;
    bool bRefreshQueued = false;
    bool bSettingsOpen = false;
    bool bUiHidden = false;
    bool bConsumeDismissPointer = false;
    int32 SettingsPage = 0;
    float PortraitRefreshTime = 0.f;
    void BuildWidgetTree();
    void BuildSettings();
    void RebuildCharacterList();
    void RefreshSettings();
    void RefreshNow();
    void ApplyVisibility();
    void UpdateResponsiveLayout(FVector2D GeometrySize);
    UTextBlock* MakeText(const FText& Text, int32 Size, FLinearColor Color);
    UViewerButton* MakeButton(const FText& Text, UTextBlock** OutLabel = nullptr);
    void AddSettingChoice(UVerticalBox* Content, const FText& Label, EViewerSetting Setting, const TArray<FString>& Options);
    void AddSettingToggle(UVerticalBox* Content, const FText& Label, EViewerSetting Setting);
    void SelectSettingsPage(int32 Page);
    UFUNCTION() void OnGraphicsSettings();
    UFUNCTION() void OnEffectsSettings();
    UFUNCTION() void OnPartsSettings();
    UFUNCTION() void OnOutfitSelected(FString SelectedItem, ESelectInfo::Type SelectionType);
    UFUNCTION() void OnPreviousOutfit();
    UFUNCTION() void OnNextOutfit();
    UFUNCTION() void OnOpenSettings();
    UFUNCTION() void OnCloseSettings();
    UFUNCTION() void OnHideUi();
    UFUNCTION() void OnShowUi();
    UFUNCTION() void OnReplay();
    UFUNCTION() void OnResetView();
    UFUNCTION() void OnRestoreDefaults();
};
