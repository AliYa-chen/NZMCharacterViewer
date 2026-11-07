#include "ViewerWidget.h"
#include "ViewerPawn.h"
#include "ViewerStyle.h"
#include "Blueprint/SlateBlueprintLibrary.h"
#include "Blueprint/WidgetLayoutLibrary.h"
#include "Blueprint/WidgetTree.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Components/Border.h"
#include "Components/CanvasPanel.h"
#include "Components/CanvasPanelSlot.h"
#include "Components/HorizontalBox.h"
#include "Components/HorizontalBoxSlot.h"
#include "Components/Image.h"
#include "Components/Overlay.h"
#include "Components/OverlaySlot.h"
#include "Components/SafeZone.h"
#include "Components/ScaleBox.h"
#include "Components/ScrollBox.h"
#include "Components/SizeBox.h"
#include "Components/TextBlock.h"
#include "Components/VerticalBox.h"
#include "Components/VerticalBoxSlot.h"
#include "Engine/Texture2D.h"

namespace ViewerUi
{
    const FLinearColor White(0.96f, 0.97f, 1.f), Muted(0.62f, 0.69f, 0.77f);
    const FLinearColor Accent(1.f, 0.77f, 0.39f), Glass(0.025f, 0.035f, 0.052f, 0.82f);
    const FLinearColor ColdBlue(0.48f, 0.76f, 0.94f, 0.92f);

    FButtonStyle ButtonStyle(FLinearColor Fill, float Radius = 8.f, FMargin Padding = FMargin(10.f, 8.f))
    {
        FButtonStyle Style;
        Style.Normal = FSlateRoundedBoxBrush(Fill, Radius);
        Style.Hovered = FSlateRoundedBoxBrush(FLinearColor(Fill.R + 0.05f, Fill.G + 0.05f, Fill.B + 0.05f, Fill.A), Radius);
        Style.Pressed = FSlateRoundedBoxBrush(FLinearColor(0.2f, 0.23f, 0.28f, 0.96f), Radius);
        Style.Disabled = FSlateRoundedBoxBrush(FLinearColor(0.035f, 0.04f, 0.05f, 0.55f), Radius);
        Style.NormalPadding = Style.PressedPadding = Padding;
        return Style;
    }

    FButtonStyle ActionStyle(bool bSelected = false)
    {
        FButtonStyle Style = ButtonStyle(FLinearColor(0.02f, 0.045f, 0.07f, bSelected ? 0.64f : 0.46f), 6.f, FMargin(6.f));
        Style.Hovered = FSlateRoundedBoxBrush(FLinearColor(0.05f, 0.1f, 0.15f, 0.62f), 6.f);
        Style.Pressed = FSlateRoundedBoxBrush(FLinearColor(0.07f, 0.16f, 0.23f, 0.78f), 6.f);
        Style.Disabled = FSlateRoundedBoxBrush(FLinearColor(0.02f, 0.045f, 0.07f, 0.24f), 6.f);
        return Style;
    }

    bool ContainsPixel(const UWidget* Widget, const UObject* Context, FVector2D Point)
    {
        if (!Widget || !Widget->IsVisible()) return false;
        const FGeometry& Geometry = Widget->GetCachedGeometry();
        if (Geometry.GetLocalSize().GetMin() <= 0.f) return false;
        FVector2D TopLeft, BottomRight, Logical;
        USlateBlueprintLibrary::LocalToViewport(Context, Geometry, FVector2D::ZeroVector, TopLeft, Logical);
        USlateBlueprintLibrary::LocalToViewport(Context, Geometry, Geometry.GetLocalSize(), BottomRight, Logical);
        return Point.X >= TopLeft.X && Point.X <= BottomRight.X && Point.Y >= TopLeft.Y && Point.Y <= BottomRight.Y;
    }

    UCanvasPanelSlot* Place(UCanvasPanel* Canvas, UWidget* Widget, FAnchors Anchors, FVector2D Alignment, int32 ZOrder = 0)
    {
        UCanvasPanelSlot* Slot = Canvas->AddChildToCanvas(Widget);
        Slot->SetAnchors(Anchors);
        Slot->SetAlignment(Alignment);
        Slot->SetZOrder(ZOrder);
        return Slot;
    }
}

UViewerButton::UViewerButton(const FObjectInitializer& ObjectInitializer) : Super(ObjectInitializer)
{
    InitIsFocusable(false);
    SetTouchMethod(EButtonTouchMethod::PreciseTap);
    SetClickMethod(EButtonClickMethod::PreciseClick);
}

UViewerComboBoxString::UViewerComboBoxString(const FObjectInitializer& ObjectInitializer) : Super(ObjectInitializer)
{
    InitFont(ViewerStyle::Font(16));
    InitIsFocusable(false);
    InitForegroundColor(FSlateColor(ViewerUi::White));
    FComboBoxStyle Combo = GetWidgetStyle();
    Combo.ComboButtonStyle.ButtonStyle = ViewerUi::ButtonStyle(FLinearColor(0.035f, 0.055f, 0.085f, 0.98f));
    Combo.ComboButtonStyle.MenuBorderBrush = FSlateRoundedBoxBrush(FLinearColor(0.025f, 0.04f, 0.065f, 1.f), 7.f);
    Combo.ComboButtonStyle.DownArrowImage.TintColor = FSlateColor(ViewerUi::Accent);
    SetWidgetStyle(Combo);
    FTableRowStyle Rows = GetItemStyle();
    Rows.TextColor = FSlateColor(ViewerUi::White);
    Rows.SelectedTextColor = FSlateColor(ViewerUi::Accent);
    Rows.EvenRowBackgroundBrush = Rows.OddRowBackgroundBrush = FSlateRoundedBoxBrush(FLinearColor(0.025f, 0.04f, 0.065f, 1.f), 0.f);
    Rows.EvenRowBackgroundHoveredBrush = Rows.OddRowBackgroundHoveredBrush = FSlateRoundedBoxBrush(FLinearColor(0.08f, 0.12f, 0.18f, 1.f), 3.f);
    Rows.ActiveBrush = Rows.InactiveBrush = FSlateRoundedBoxBrush(FLinearColor(0.12f, 0.16f, 0.22f, 1.f), 3.f);
    SetItemStyle(Rows);
    SetContentPadding(FMargin(12.f, 9.f));
    SetMaxListHeight(240.f);
}

void UViewerCharacterTile::Configure(UViewerWidget* InHost, AViewerPawn* InViewer, int32 InIndex)
{
    Host = InHost; Viewer = InViewer; Index = InIndex;
    CardSize = NewObject<USizeBox>(this);
    AddChild(CardSize);
    UOverlay* Overlay = NewObject<UOverlay>(this);
    CardSize->AddChild(Overlay);
    UScaleBox* Scale = NewObject<UScaleBox>(this);
    Scale->SetStretch(EStretch::ScaleToFit);
    UOverlaySlot* ImageSlot = Overlay->AddChildToOverlay(Scale);
    ImageSlot->SetHorizontalAlignment(HAlign_Fill);
    ImageSlot->SetVerticalAlignment(VAlign_Fill);
    Portrait = NewObject<UImage>(this);
    Scale->AddChild(Portrait);
    FallbackText = NewObject<UTextBlock>(this);
    FallbackText->SetFont(ViewerStyle::Font(26));
    FallbackText->SetColorAndOpacity(FSlateColor(ViewerUi::Muted));
    UOverlaySlot* FallbackSlot = Overlay->AddChildToOverlay(FallbackText);
    FallbackSlot->SetHorizontalAlignment(HAlign_Center);
    FallbackSlot->SetVerticalAlignment(VAlign_Center);
    UBorder* Plate = NewObject<UBorder>(this);
    Plate->SetBrush(FSlateRoundedBoxBrush(FLinearColor(0.015f, 0.022f, 0.035f, 0.66f), 0.f));
    Plate->SetPadding(FMargin(4.f, 6.f));
    UOverlaySlot* NameSlot = Overlay->AddChildToOverlay(Plate);
    NameSlot->SetHorizontalAlignment(HAlign_Fill);
    NameSlot->SetVerticalAlignment(VAlign_Bottom);
    NameText = NewObject<UTextBlock>(this);
    NameText->SetJustification(ETextJustify::Center);
    NameText->SetAutoWrapText(true);
    Plate->AddChild(NameText);
    if (Viewer)
    {
        const FText Name = Viewer->GetCharacterDisplayName(Index);
        NameText->SetText(Name);
        FallbackText->SetText(FText::FromString(Name.ToString().Left(1)));
    }
    OnClicked.AddUniqueDynamic(this, &UViewerCharacterTile::OnTileClicked);
    SetCardSize(96.f, 126.f, false);
    UpdatePortrait();
    SetSelected(false);
}

void UViewerCharacterTile::UpdatePortrait()
{
    if (!Viewer || !Portrait) return;
    UTexture2D* Texture = Viewer->GetCharacterPortrait(Index);
    if (Texture != LastPortrait)
    {
        LastPortrait = Texture;
        Portrait->SetBrushFromTexture(Texture, true);
    }
    Portrait->SetVisibility(Texture ? ESlateVisibility::HitTestInvisible : ESlateVisibility::Collapsed);
    FallbackText->SetVisibility(Texture ? ESlateVisibility::Collapsed : ESlateVisibility::HitTestInvisible);
}

void UViewerCharacterTile::SetSelected(bool bSelected)
{
    const FLinearColor Fill(0.02f, 0.045f, 0.07f, bSelected ? 0.62f : 0.36f);
    FButtonStyle Style = ViewerUi::ButtonStyle(Fill, 0.f, FMargin(2.f));
    Style.Normal = FSlateRoundedBoxBrush(Fill, 0.f, bSelected ? ViewerUi::ColdBlue : FLinearColor(0.22f, 0.34f, 0.44f, 0.45f), bSelected ? 1.5f : 0.75f);
    Style.Hovered = FSlateRoundedBoxBrush(FLinearColor(0.035f, 0.075f, 0.11f, 0.7f), 0.f, ViewerUi::ColdBlue, 1.5f);
    Style.Pressed = FSlateRoundedBoxBrush(FLinearColor(0.05f, 0.11f, 0.16f, 0.8f), 0.f, ViewerUi::ColdBlue, 1.5f);
    SetStyle(Style);
    if (NameText) NameText->SetColorAndOpacity(FSlateColor(bSelected ? ViewerUi::ColdBlue : ViewerUi::White));
    SetRenderOpacity(bSelected ? 1.f : 0.9f);
}

void UViewerCharacterTile::SetCardSize(float Width, float Height, bool bCompact)
{
    if (CardSize) { CardSize->SetWidthOverride(Width); CardSize->SetHeightOverride(Height); }
    if (NameText) NameText->SetFont(ViewerStyle::Font(bCompact ? 11 : 13));
}
void UViewerCharacterTile::OnTileClicked() { if (Host) Host->SelectCharacterTile(Index); }

void UViewerSettingPicker::Configure(UViewerWidget* InHost, AViewerPawn* InViewer, EViewerSetting InSetting, const TArray<FString>& Options)
{
    Host = InHost; Viewer = InViewer; Setting = InSetting;
    TGuardValue<bool> Guard(bUpdating, true);
    ClearOptions();
    for (const FString& Option : Options) AddOption(Option);
    OnSelectionChanged.AddUniqueDynamic(this, &UViewerSettingPicker::OnChoiceChanged);
}
void UViewerSettingPicker::Sync(const UViewerPreferences* Preferences)
{
    TGuardValue<bool> Guard(bUpdating, true);
    SetIsEnabled(Preferences != nullptr);
    if (Preferences) SetSelectedIndex(FMath::Clamp(Preferences->GetChoice(Setting), 0, FMath::Max(0, GetOptionCount() - 1)));
}
void UViewerSettingPicker::OnChoiceChanged(FString SelectedItem, ESelectInfo::Type SelectionType)
{
    if (bUpdating || !Viewer || GetSelectedIndex() < 0) return;
    Viewer->SetPreferenceChoice(Setting, GetSelectedIndex());
    if (Host) Host->Refresh();
}

void UViewerSettingToggle::Configure(UViewerWidget* InHost, AViewerPawn* InViewer, EViewerSetting InSetting, const FText& Label)
{
    Host = InHost; Viewer = InViewer; Setting = InSetting;
    FCheckBoxStyle Style = GetWidgetStyle();
    Style.UncheckedImage = FSlateRoundedBoxBrush(FLinearColor(0.03f, 0.05f, 0.075f, 1.f), 6.f, ViewerUi::Muted, 1.f);
    Style.CheckedImage = FSlateRoundedBoxBrush(ViewerUi::Accent, 6.f);
    Style.UncheckedHoveredImage = Style.UncheckedPressedImage = Style.UncheckedImage;
    Style.CheckedHoveredImage = Style.CheckedPressedImage = Style.CheckedImage;
    Style.UncheckedImage.ImageSize = Style.UncheckedHoveredImage.ImageSize = Style.UncheckedPressedImage.ImageSize = FVector2D(26.f);
    Style.CheckedImage.ImageSize = Style.CheckedHoveredImage.ImageSize = Style.CheckedPressedImage.ImageSize = FVector2D(26.f);
    SetWidgetStyle(Style);
    UTextBlock* Text = NewObject<UTextBlock>(this);
    Text->SetText(Label); Text->SetFont(ViewerStyle::Font(16));
    Text->SetColorAndOpacity(FSlateColor(ViewerUi::White));
    Text->SetMargin(FMargin(12.f, 13.f)); AddChild(Text);
    OnCheckStateChanged.AddUniqueDynamic(this, &UViewerSettingToggle::OnToggleChanged);
}
void UViewerSettingToggle::Sync(const UViewerPreferences* Preferences)
{
    TGuardValue<bool> Guard(bUpdating, true);
    if (Preferences) SetIsChecked(Preferences->GetToggle(Setting));
    const bool Dependent = Setting == EViewerSetting::Bloom || Setting == EViewerSetting::Vignette || Setting == EViewerSetting::DepthOfField
        || Setting == EViewerSetting::AmbientOcclusion || Setting == EViewerSetting::ScreenSpaceReflections;
    SetIsEnabled(Preferences && (!Dependent || Preferences->bPostProcessing));
}
void UViewerSettingToggle::OnToggleChanged(bool bChecked)
{
    if (bUpdating || !Viewer) return;
    Viewer->SetPreferenceToggle(Setting, bChecked);
    if (Host) Host->Refresh();
}
void UViewerPartToggle::Configure(AViewerPawn* InViewer, int32 InIndex, const FText& Label)
{
    Viewer = InViewer; Index = InIndex;
    UTextBlock* Text = NewObject<UTextBlock>(this);
    Text->SetText(Label); Text->SetFont(ViewerStyle::Font(13));
    Text->SetColorAndOpacity(FSlateColor(ViewerUi::Muted));
    Text->SetAutoWrapText(true); Text->SetMargin(FMargin(10.f, 7.f)); AddChild(Text);
    SetIsChecked(Viewer && Viewer->IsPartVisible(Index));
    SetIsEnabled(Viewer && Viewer->IsPartLoaded(Index));
    OnCheckStateChanged.AddUniqueDynamic(this, &UViewerPartToggle::OnChanged);
}
void UViewerPartToggle::OnChanged(bool bChecked) { if (Viewer) Viewer->SetPartVisible(Index, bChecked); }

void UViewerWidget::InitializeViewer(AViewerPawn* InViewer) { Viewer = InViewer; }
UTextBlock* UViewerWidget::MakeText(const FText& Text, int32 Size, FLinearColor Color)
{
    UTextBlock* Result = WidgetTree->ConstructWidget<UTextBlock>();
    Result->SetText(Text); Result->SetFont(ViewerStyle::Font(Size));
    Result->SetColorAndOpacity(FSlateColor(Color)); Result->SetAutoWrapText(true);
    return Result;
}
UViewerButton* UViewerWidget::MakeButton(const FText& Text, UTextBlock** OutLabel)
{
    UViewerButton* Button = WidgetTree->ConstructWidget<UViewerButton>();
    Button->SetStyle(ViewerUi::ButtonStyle(ViewerUi::Glass));
    UTextBlock* Label = MakeText(Text, 13, ViewerUi::White);
    Label->SetAutoWrapText(false);
    Label->SetJustification(ETextJustify::Center); Button->AddChild(Label);
    if (OutLabel) *OutLabel = Label;
    return Button;
}
TSharedRef<SWidget> UViewerWidget::RebuildWidget()
{
    if (!WidgetTree) WidgetTree = NewObject<UWidgetTree>(this, TEXT("WidgetTree"));
    if (!WidgetTree->RootWidget) BuildWidgetTree();
    return Super::RebuildWidget();
}
void UViewerWidget::NativeConstruct()
{
    Super::NativeConstruct(); Refresh();
    UpdateResponsiveLayout(GetCachedGeometry().GetLocalSize());
}
void UViewerWidget::NativeTick(const FGeometry& MyGeometry, float InDeltaTime)
{
    Super::NativeTick(MyGeometry, InDeltaTime);
    // Defer changes to widget children until dynamic selection/check delegates
    // have returned. Pawn preference setters can also call Refresh synchronously.
    if (bRefreshQueued)
    {
        bRefreshQueued = false;
        RefreshNow();
    }
    UpdateResponsiveLayout(MyGeometry.GetLocalSize());
    PortraitRefreshTime += InDeltaTime;
    if (PortraitRefreshTime >= 0.5f)
    {
        PortraitRefreshTime = 0.f;
        for (UViewerCharacterTile* Tile : CharacterTiles) if (Tile) Tile->UpdatePortrait();
    }
}

void UViewerWidget::BuildWidgetTree()
{
    UCanvasPanel* Root = WidgetTree->ConstructWidget<UCanvasPanel>();
    Root->SetVisibility(ESlateVisibility::SelfHitTestInvisible); WidgetTree->RootWidget = Root;
    USafeZone* Safe = WidgetTree->ConstructWidget<USafeZone>();
    Safe->SetVisibility(ESlateVisibility::SelfHitTestInvisible);
    ViewerUi::Place(Root, Safe, FAnchors(0.f, 0.f, 1.f, 1.f), FVector2D::ZeroVector)->SetOffsets(FMargin(0.f));
    LayoutCanvas = WidgetTree->ConstructWidget<UCanvasPanel>();
    LayoutCanvas->SetVisibility(ESlateVisibility::SelfHitTestInvisible); Safe->AddChild(LayoutCanvas);
    Header = WidgetTree->ConstructWidget<UVerticalBox>();
    Header->SetVisibility(ESlateVisibility::HitTestInvisible);
    CharacterLabel = MakeText(FText::FromString(TEXT("人物预览")), 13, ViewerUi::Accent);
    Header->AddChildToVerticalBox(CharacterLabel)->SetPadding(FMargin(0.f, 0.f, 0.f, 4.f));
    UHorizontalBox* TitleRow = WidgetTree->ConstructWidget<UHorizontalBox>();
    Header->AddChildToVerticalBox(TitleRow);
    TitleAccentSize = WidgetTree->ConstructWidget<USizeBox>();
    TitleAccentSize->SetWidthOverride(3.f);
    TitleAccentSize->SetHeightOverride(38.f);
    UBorder* TitleAccent = WidgetTree->ConstructWidget<UBorder>();
    TitleAccent->SetBrush(FSlateRoundedBoxBrush(ViewerUi::Accent, 0.f));
    TitleAccentSize->AddChild(TitleAccent);
    UHorizontalBoxSlot* AccentSlot = TitleRow->AddChildToHorizontalBox(TitleAccentSize);
    AccentSlot->SetVerticalAlignment(VAlign_Center);
    AccentSlot->SetPadding(FMargin(0.f, 0.f, 10.f, 0.f));
    OutfitTitle = MakeText(FText::GetEmpty(), 34, ViewerUi::White);
    OutfitTitle->SetAutoWrapText(false);
    OutfitTitle->SetTextOverflowPolicy(ETextOverflowPolicy::Ellipsis);
    TitleRow->AddChildToHorizontalBox(OutfitTitle)->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
    StatusText = MakeText(FText::GetEmpty(), 11, ViewerUi::Muted);
    Header->AddChildToVerticalBox(StatusText)->SetPadding(FMargin(0.f, 4.f));
    HeaderSlot = ViewerUi::Place(LayoutCanvas, Header, FAnchors(0.f, 0.f), FVector2D::ZeroVector);
    CharacterRail = WidgetTree->ConstructWidget<UBorder>();
    CharacterRail->SetBrush(FSlateRoundedBoxBrush(FLinearColor::Transparent, 0.f));
    CharacterRail->SetPadding(FMargin(4.f));
    CharacterScroll = WidgetTree->ConstructWidget<UScrollBox>();
    CharacterScroll->SetOrientation(Orient_Vertical);
    CharacterScroll->SetScrollBarVisibility(ESlateVisibility::Collapsed);
    CharacterScroll->SetConsumeMouseWheel(EConsumeMouseWheel::WhenScrollingPossible);
    CharacterScroll->SetAnimateWheelScrolling(true); CharacterRail->AddChild(CharacterScroll);
    CharacterList = WidgetTree->ConstructWidget<UVerticalBox>(); CharacterScroll->AddChild(CharacterList);
    RailSlot = ViewerUi::Place(LayoutCanvas, CharacterRail, FAnchors(0.f, 0.f), FVector2D::ZeroVector);
    ActionBar = WidgetTree->ConstructWidget<UVerticalBox>();
    ActionsSlot = ViewerUi::Place(LayoutCanvas, ActionBar, FAnchors(1.f, 0.f), FVector2D(1.f, 0.f));
    ActionsSlot->SetAutoSize(true);
    auto AddAction = [this](const FText& Label, const TCHAR* Icon = nullptr) -> UViewerButton*
    {
        UTextBlock* Text = nullptr; UViewerButton* Button = MakeButton(Label, &Text);
        if (Icon)
        {
            UTexture2D* Texture = LoadObject<UTexture2D>(nullptr,
                *FString::Printf(TEXT("/Game/Viewer/UI/T_%s.T_%s"), Icon, Icon));
            if (Texture)
            {
                Button->ClearChildren();
                UVerticalBox* Stack = WidgetTree->ConstructWidget<UVerticalBox>();
                UImage* Image = WidgetTree->ConstructWidget<UImage>();
                Image->SetBrushFromTexture(Texture);
                Image->SetColorAndOpacity(FLinearColor(0.693872f, 0.89627f, 1.f, 0.7f));
                USizeBox* IconSize = WidgetTree->ConstructWidget<USizeBox>();
                IconSize->SetWidthOverride(28.f); IconSize->SetHeightOverride(28.f);
                IconSize->AddChild(Image);
                Stack->AddChildToVerticalBox(IconSize)->SetHorizontalAlignment(HAlign_Center);
                Stack->AddChildToVerticalBox(Text)->SetHorizontalAlignment(HAlign_Center);
                Button->AddChild(Stack);
            }
        }
        Button->SetStyle(ViewerUi::ActionStyle());
        Text->SetColorAndOpacity(FSlateColor(ViewerUi::ColdBlue));
        USizeBox* Size = WidgetTree->ConstructWidget<USizeBox>();
        Size->SetWidthOverride(92.f); Size->SetHeightOverride(44.f); Size->AddChild(Button);
        ActionBar->AddChildToVerticalBox(Size)->SetPadding(FMargin(0.f, 0.f, 0.f, 8.f));
        ActionSizes.Add(Size); ActionLabels.Add(Text); return Button;
    };
    UViewerButton* Open = AddAction(FText::FromString(TEXT("设置"))); SettingsButton = Open;
    Open->OnClicked.AddDynamic(this, &UViewerWidget::OnOpenSettings);
    AddAction(FText::FromString(TEXT("隐藏UI")), TEXT("HideUI"))->OnClicked.AddDynamic(this, &UViewerWidget::OnHideUi);
    AddAction(FText::FromString(TEXT("重播")), TEXT("Replay"))->OnClicked.AddDynamic(this, &UViewerWidget::OnReplay);
    AddAction(FText::FromString(TEXT("复位")))->OnClicked.AddDynamic(this, &UViewerWidget::OnResetView);
    Footer = WidgetTree->ConstructWidget<UBorder>();
    Footer->SetBrush(FSlateRoundedBoxBrush(FLinearColor(0.015f, 0.028f, 0.045f, 0.38f), 1.f)); Footer->SetPadding(FMargin(10.f, 8.f));
    UVerticalBox* FooterContent = WidgetTree->ConstructWidget<UVerticalBox>(); Footer->AddChild(FooterContent);
    FooterContent->AddChildToVerticalBox(MakeText(FText::FromString(TEXT("服装")), 11, ViewerUi::Accent))->SetPadding(FMargin(0.f, 0.f, 0.f, 6.f));
    UHorizontalBox* Row = WidgetTree->ConstructWidget<UHorizontalBox>(); FooterContent->AddChildToVerticalBox(Row);
    PreviousOutfit = MakeButton(FText::FromString(TEXT("上一套")));
    PreviousOutfit->SetStyle(ViewerUi::ActionStyle());
    PreviousOutfit->OnClicked.AddDynamic(this, &UViewerWidget::OnPreviousOutfit);
    Row->AddChildToHorizontalBox(PreviousOutfit)->SetPadding(FMargin(0.f, 0.f, 6.f, 0.f));
    OutfitPicker = WidgetTree->ConstructWidget<UViewerComboBoxString>();
    FComboBoxStyle OutfitStyle = OutfitPicker->GetWidgetStyle();
    OutfitStyle.ComboButtonStyle.ButtonStyle = ViewerUi::ButtonStyle(FLinearColor(0.025f, 0.045f, 0.07f, 0.46f), 0.f, FMargin(8.f, 6.f));
    OutfitPicker->SetWidgetStyle(OutfitStyle);
    OutfitPicker->SetContentPadding(FMargin(8.f, 4.f));
    OutfitPicker->OnSelectionChanged.AddDynamic(this, &UViewerWidget::OnOutfitSelected);
    Row->AddChildToHorizontalBox(OutfitPicker)->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
    NextOutfit = MakeButton(FText::FromString(TEXT("下一套")));
    NextOutfit->SetStyle(ViewerUi::ActionStyle());
    NextOutfit->OnClicked.AddDynamic(this, &UViewerWidget::OnNextOutfit);
    Row->AddChildToHorizontalBox(NextOutfit)->SetPadding(FMargin(6.f, 0.f, 0.f, 0.f));
    FooterSlot = ViewerUi::Place(LayoutCanvas, Footer, FAnchors(0.f, 1.f), FVector2D(0.f, 1.f));
    BuildSettings();
    RestoreControl = WidgetTree->ConstructWidget<USizeBox>(); RestoreControl->SetWidthOverride(94.f); RestoreControl->SetHeightOverride(44.f);
    UViewerButton* Restore = MakeButton(FText::FromString(TEXT("显示界面")));
    Restore->OnClicked.AddDynamic(this, &UViewerWidget::OnShowUi); RestoreControl->AddChild(Restore);
    RestoreSlot = ViewerUi::Place(LayoutCanvas, RestoreControl, FAnchors(1.f, 0.f), FVector2D(1.f, 0.f), 200);
    RestoreSlot->SetAutoSize(true);
    ApplyVisibility();
}

void UViewerWidget::AddSettingChoice(UVerticalBox* Content, const FText& Label, EViewerSetting Setting, const TArray<FString>& Options)
{
    UHorizontalBox* Row = WidgetTree->ConstructWidget<UHorizontalBox>();
    Content->AddChildToVerticalBox(Row)->SetPadding(FMargin(0.f, 7.f));
    USizeBox* LabelSize = WidgetTree->ConstructWidget<USizeBox>();
    LabelSize->SetWidthOverride(112.f);
    LabelSize->AddChild(MakeText(Label, 15, ViewerUi::White));
    Row->AddChildToHorizontalBox(LabelSize)->SetVerticalAlignment(VAlign_Center);
    UViewerSettingPicker* Picker = WidgetTree->ConstructWidget<UViewerSettingPicker>();
    Picker->Configure(this, Viewer, Setting, Options);
    Picker->SetContentPadding(FMargin(14.f, 13.f));
    Picker->SetMaxListHeight(300.f);
    Row->AddChildToHorizontalBox(Picker)->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
    ChoicePickers.Add(Picker);
}
void UViewerWidget::AddSettingToggle(UVerticalBox* Content, const FText& Label, EViewerSetting Setting)
{
    UViewerSettingToggle* Toggle = WidgetTree->ConstructWidget<UViewerSettingToggle>();
    Toggle->Configure(this, Viewer, Setting, Label);
    Content->AddChildToVerticalBox(Toggle)->SetPadding(FMargin(0.f, 2.f)); SettingToggles.Add(Toggle);
}
void UViewerWidget::BuildSettings()
{
    SettingsOverlay = WidgetTree->ConstructWidget<UCanvasPanel>();
    ViewerUi::Place(LayoutCanvas, SettingsOverlay, FAnchors(0.f, 0.f, 1.f, 1.f), FVector2D::ZeroVector, 100)->SetOffsets(FMargin(0.f));
    UViewerButton* Dimmer = WidgetTree->ConstructWidget<UViewerButton>();
    Dimmer->SetStyle(ViewerUi::ButtonStyle(FLinearColor(0.f, 0.f, 0.f, 0.48f), 0.f, FMargin(0.f)));
    // Dismiss on press so a tap outside never starts a model drag behind it.
    Dimmer->SetClickMethod(EButtonClickMethod::MouseDown);
    Dimmer->SetTouchMethod(EButtonTouchMethod::Down);
    Dimmer->OnClicked.AddDynamic(this, &UViewerWidget::OnCloseSettings);
    ViewerUi::Place(SettingsOverlay, Dimmer, FAnchors(0.f, 0.f, 1.f, 1.f), FVector2D::ZeroVector)->SetOffsets(FMargin(0.f));
    SettingsPanel = WidgetTree->ConstructWidget<UBorder>();
    SettingsPanel->SetBrush(FSlateRoundedBoxBrush(FLinearColor(0.025f, 0.035f, 0.052f, 0.98f), 12.f));
    SettingsPanel->SetPadding(FMargin(22.f));
    SettingsSlot = ViewerUi::Place(SettingsOverlay, SettingsPanel, FAnchors(1.f, 0.f), FVector2D(1.f, 0.f), 1);
    UVerticalBox* Panel = WidgetTree->ConstructWidget<UVerticalBox>(); SettingsPanel->AddChild(Panel);
    UHorizontalBox* Top = WidgetTree->ConstructWidget<UHorizontalBox>();
    Panel->AddChildToVerticalBox(Top)->SetPadding(FMargin(0.f, 0.f, 0.f, 10.f));
    Top->AddChildToHorizontalBox(MakeText(FText::FromString(TEXT("设置")), 24, ViewerUi::White))->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
    UViewerButton* Close = MakeButton(FText::FromString(TEXT("关闭")));
    Close->OnClicked.AddDynamic(this, &UViewerWidget::OnCloseSettings); Top->AddChildToHorizontalBox(Close);
    UHorizontalBox* Tabs = WidgetTree->ConstructWidget<UHorizontalBox>();
    Panel->AddChildToVerticalBox(Tabs)->SetPadding(FMargin(0.f, 4.f, 0.f, 16.f));
    for (const TCHAR* Label : { TEXT("画面"), TEXT("效果"), TEXT("部件") })
    {
        UViewerButton* Tab = MakeButton(FText::FromString(Label));
        Tabs->AddChildToHorizontalBox(Tab)->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
        SettingsTabs.Add(Tab);
    }
    SettingsTabs[0]->OnClicked.AddDynamic(this, &UViewerWidget::OnGraphicsSettings);
    SettingsTabs[1]->OnClicked.AddDynamic(this, &UViewerWidget::OnEffectsSettings);
    SettingsTabs[2]->OnClicked.AddDynamic(this, &UViewerWidget::OnPartsSettings);
    SettingsScroll = WidgetTree->ConstructWidget<UScrollBox>(); SettingsScroll->SetOrientation(Orient_Vertical);
    SettingsScroll->SetAnimateWheelScrolling(true); SettingsScroll->SetConsumeMouseWheel(EConsumeMouseWheel::WhenScrollingPossible);
    Panel->AddChildToVerticalBox(SettingsScroll)->SetSize(FSlateChildSize(ESlateSizeRule::Fill));
    UVerticalBox* Content = WidgetTree->ConstructWidget<UVerticalBox>(); SettingsScroll->AddChild(Content);
    GraphicsSettings = WidgetTree->ConstructWidget<UVerticalBox>(); Content->AddChildToVerticalBox(GraphicsSettings);
    EffectsSettings = WidgetTree->ConstructWidget<UVerticalBox>(); Content->AddChildToVerticalBox(EffectsSettings);
    PartsSettings = WidgetTree->ConstructWidget<UVerticalBox>(); Content->AddChildToVerticalBox(PartsSettings);
    TArray<FString> Rates;
    for (int32 Rate : UViewerPreferences::FrameRates()) Rates.Add(FString::Printf(TEXT("%d 帧 / 秒"), Rate));
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("画质预设")), EViewerSetting::Quality, {TEXT("低"), TEXT("均衡"), TEXT("高"), TEXT("极致"), TEXT("自定义")});
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("帧率上限")), EViewerSetting::FrameRate, Rates);
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("分辨率")), EViewerSetting::Resolution, {TEXT("70%"), TEXT("100%"), TEXT("125%"), TEXT("150%"), TEXT("200%")});
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("纹理")), EViewerSetting::Textures, {TEXT("低"), TEXT("均衡"), TEXT("高"), TEXT("极致")});
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("阴影")), EViewerSetting::Shadows, {TEXT("关闭"), TEXT("低"), TEXT("高"), TEXT("极致")});
    TArray<FString> AntialiasingOptions;
    for (int32 Samples : UViewerPreferences::AntialiasingSamples())
        AntialiasingOptions.Add(Samples == 0 ? TEXT("TAA") : Samples == 1 ? TEXT("关闭")
            : FString::Printf(TEXT("%d× MSAA"), Samples));
    AddSettingChoice(GraphicsSettings, FText::FromString(TEXT("抗锯齿")), EViewerSetting::Antialiasing, AntialiasingOptions);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("后期处理")), EViewerSetting::PostProcessing);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("泛光")), EViewerSetting::Bloom);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("暗角")), EViewerSetting::Vignette);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("景深")), EViewerSetting::DepthOfField);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("环境光遮蔽")), EViewerSetting::AmbientOcclusion);
    AddSettingToggle(EffectsSettings, FText::FromString(UViewerPreferences::UsesMobileRenderer()
        ? TEXT("屏幕空间反射 · TAA") : TEXT("Lumen 反射")), EViewerSetting::ScreenSpaceReflections);
    AddSettingToggle(EffectsSettings, FText::FromString(TEXT("发丝与衣物动态")), EViewerSetting::SecondaryMotion);
    if (UViewerPreferences::UsesMobileRenderer())
        EffectsSettings->AddChildToVerticalBox(MakeText(FText::FromString(TEXT("当前移动渲染模式支持屏幕空间反射，不支持 Lumen 与硬件光追。")), 12, ViewerUi::Muted))->SetPadding(FMargin(0.f, 16.f));
    PartList = WidgetTree->ConstructWidget<UVerticalBox>(); PartsSettings->AddChildToVerticalBox(PartList);
    RestoreDefaultsButton = MakeButton(FText::FromString(TEXT("恢复默认设置")));
    RestoreDefaultsButton->OnClicked.AddDynamic(this, &UViewerWidget::OnRestoreDefaults);
    Panel->AddChildToVerticalBox(RestoreDefaultsButton)->SetPadding(FMargin(0.f, 16.f, 0.f, 0.f));
    SelectSettingsPage(0);
}
void UViewerWidget::RebuildCharacterList()
{
    CharacterList->ClearChildren(); CharacterTiles.Reset();
    if (!Viewer) return;
    for (int32 Index = 0; Index < Viewer->GetCharacterCount(); ++Index)
    {
        UViewerCharacterTile* Tile = WidgetTree->ConstructWidget<UViewerCharacterTile>();
        Tile->Configure(this, Viewer, Index);
        CharacterList->AddChildToVerticalBox(Tile)->SetPadding(FMargin(0.f, 0.f, 0.f, 9.f)); CharacterTiles.Add(Tile);
    }
    LastLayoutSize = FVector2D::ZeroVector;
}
void UViewerWidget::RefreshSettings()
{
    const UViewerPreferences* Preferences = Viewer ? Viewer->GetPreferences() : nullptr;
    for (UViewerSettingPicker* Picker : ChoicePickers) if (Picker) Picker->Sync(Preferences);
    for (UViewerSettingToggle* Toggle : SettingToggles) if (Toggle) Toggle->Sync(Preferences);
    if (RestoreDefaultsButton) RestoreDefaultsButton->SetIsEnabled(Preferences != nullptr);
    if (!PartList) return;
    PartList->ClearChildren();
    if (Viewer)
        if (const FViewerOutfit* Outfit = Viewer->GetCurrentOutfit())
            for (int32 Index = 0; Index < Outfit->Parts.Num(); ++Index)
            {
                UViewerPartToggle* Toggle = WidgetTree->ConstructWidget<UViewerPartToggle>();
                Toggle->Configure(Viewer, Index, Outfit->Parts[Index].DisplayName);
                PartList->AddChildToVerticalBox(Toggle)->SetPadding(FMargin(0.f, 2.f));
            }
}
void UViewerWidget::Refresh()
{
    bRefreshQueued = true;
}

void UViewerWidget::RefreshNow()
{
    if (!LayoutCanvas || !Viewer) return;
    TGuardValue<bool> Guard(bRefreshing, true);
    if (CharacterTiles.Num() != Viewer->GetCharacterCount()) RebuildCharacterList();
    for (int32 Index = 0; Index < CharacterTiles.Num(); ++Index)
    {
        CharacterTiles[Index]->SetSelected(Index == Viewer->GetCharacterIndex()); CharacterTiles[Index]->UpdatePortrait();
    }
    CharacterLabel->SetText(FText::Format(FText::FromString(TEXT("‹ 角色  ·  {0}")), Viewer->GetCharacterLabel()));
    OutfitTitle->SetText(Viewer->GetCurrentOutfit() ? Viewer->GetOutfitLabel() : FText::FromString(TEXT("人物预览")));
    const FString Status = Viewer->GetStatusLabel().ToString();
    const bool Failed = Status.Contains(TEXT("未能")) || Status.Contains(TEXT("无法"));
    StatusText->SetText(Failed ? FText::FromString(TEXT("载入失败，请重新选择"))
        : Status.Contains(TEXT("载入")) ? FText::FromString(TEXT("正在载入…")) : FText::GetEmpty());
    const TArray<FString> Options = Viewer->GetOutfitOptions();
    if (CachedOutfitOptions != Options)
    {
        CachedOutfitOptions = Options; OutfitPicker->ClearOptions();
        for (const FString& Label : Options) OutfitPicker->AddOption(Label);
    }
    if (!Options.IsEmpty()) OutfitPicker->SetSelectedIndex(Viewer->GetOutfitIndex());
    OutfitPicker->SetIsEnabled(!Options.IsEmpty());
    PreviousOutfit->SetIsEnabled(Viewer->HasMultipleOutfits()); NextOutfit->SetIsEnabled(Viewer->HasMultipleOutfits());
    RefreshSettings(); ApplyVisibility();
}
void UViewerWidget::UpdateResponsiveLayout(FVector2D GeometrySize)
{
    if (!LayoutCanvas || !RailSlot) return;
    const FVector2D Pixels = UWidgetLayoutLibrary::GetViewportSize(this);
    const float Dpi = FMath::Max(UWidgetLayoutLibrary::GetViewportScale(this), 0.01f);
    FVector2D Size = LayoutCanvas->GetCachedGeometry().GetLocalSize();
    if (Size.GetMin() <= 1.f) Size = GeometrySize;
    if (Size.GetMin() <= 1.f) Size = Pixels / Dpi;
    if (Size.GetMin() <= 1.f || (Size.Equals(LastLayoutSize, 0.5f) && Pixels.Equals(LastViewportPixels, 0.5f))) return;
    LastLayoutSize = Size; LastViewportPixels = Pixels;
    const float Aspect = Pixels.GetMin() > 0.f ? static_cast<float>(Pixels.X / Pixels.Y) : static_cast<float>(Size.X / Size.Y);
    const bool Narrow = Size.X < 540.f;
    const bool Compact = Size.X < 850.f || Aspect < 1.15f;
    const float Margin = Narrow ? 8.f : Compact ? 12.f : 22.f;
    const float RailWidth = Narrow ? 72.f : Compact ? 98.f : 126.f;
    const float ActionWidth = Narrow ? 74.f : Compact ? 88.f : 108.f;
    const float HeaderHeight = Narrow ? 104.f : Compact ? 110.f : 128.f;
    const float FooterHeight = Narrow ? 76.f : 80.f;
    const float ContentLeft = Margin + RailWidth + Margin;
    const float ContentRight = FMath::Max(ContentLeft + 1.f, static_cast<float>(Size.X) - ActionWidth - Margin * 2.f);
    RailSlot->SetPosition(FVector2D(Margin, HeaderHeight));
    RailSlot->SetSize(FVector2D(RailWidth, FMath::Max(1.f, static_cast<float>(Size.Y) - HeaderHeight - Margin)));
    HeaderSlot->SetPosition(FVector2D(ContentLeft, Margin + 8.f));
    HeaderSlot->SetSize(FVector2D(FMath::Max(1.f, ContentRight - ContentLeft), HeaderHeight - Margin));
    CharacterLabel->SetFont(ViewerStyle::Font(Narrow ? 10 : 12)); OutfitTitle->SetFont(ViewerStyle::Font(Narrow ? 20 : Compact ? 26 : 34));
    TitleAccentSize->SetHeightOverride(Narrow ? 24.f : Compact ? 30.f : 38.f);
    ActionsSlot->SetPosition(FVector2D(-Margin, Margin + 8.f));
    for (USizeBox* Action : ActionSizes) { Action->SetWidthOverride(ActionWidth); Action->SetHeightOverride(Narrow ? 60.f : 66.f); }
    for (int32 Index = 0; Index < ActionLabels.Num(); ++Index)
        ActionLabels[Index]->SetFont(ViewerStyle::Font(Index == 0 ? 17 : Narrow ? 11 : 12));
    const float AvailableFooter = FMath::Max(1.f, static_cast<float>(Size.X) - ContentLeft - Margin);
    const float FooterWidth = FMath::Min(480.f, AvailableFooter);
    FooterSlot->SetPosition(FVector2D(ContentLeft + (AvailableFooter - FooterWidth) * 0.5f, -Margin)); FooterSlot->SetSize(FVector2D(FooterWidth, FooterHeight));
    OutfitPicker->SetMaxListHeight(FMath::Max(80.f, FMath::Min(260.f, static_cast<float>(Size.Y) * 0.45f)));
    const float PanelWidth = FMath::Max(1.f, FMath::Min(470.f, static_cast<float>(Size.X) - Margin * 2.f));
    SettingsSlot->SetPosition(FVector2D(-Margin, Margin)); SettingsSlot->SetSize(FVector2D(PanelWidth, FMath::Max(1.f, static_cast<float>(Size.Y) - Margin * 2.f)));
    RestoreSlot->SetPosition(FVector2D(-Margin, Margin + 8.f));
    for (UViewerCharacterTile* Tile : CharacterTiles) Tile->SetCardSize(RailWidth - 12.f, Narrow ? 94.f : Compact ? 122.f : 154.f, Compact);
}
void UViewerWidget::ApplyVisibility()
{
    if (!CharacterRail) return;
    const bool Hidden = bUiHidden;
    const ESlateVisibility Main = Hidden ? ESlateVisibility::Collapsed : ESlateVisibility::Visible;
    CharacterRail->SetVisibility(Main); Header->SetVisibility(Hidden ? ESlateVisibility::Collapsed : ESlateVisibility::HitTestInvisible);
    ActionBar->SetVisibility(Main); Footer->SetVisibility(Main);
    SettingsOverlay->SetVisibility(!Hidden && bSettingsOpen ? ESlateVisibility::SelfHitTestInvisible : ESlateVisibility::Collapsed);
    RestoreControl->SetVisibility(bUiHidden ? ESlateVisibility::Visible : ESlateVisibility::Collapsed);
    if (SettingsButton) SettingsButton->SetStyle(ViewerUi::ButtonStyle(bSettingsOpen
        ? FLinearColor(0.10f, 0.20f, 0.29f, 0.96f) : FLinearColor(0.025f, 0.055f, 0.085f, 0.78f), 8.f));
}
void UViewerWidget::SelectSettingsPage(int32 Page)
{
    SettingsPage = FMath::Clamp(Page, 0, 2);
    const TArray<UVerticalBox*> Pages{ GraphicsSettings, EffectsSettings, PartsSettings };
    for (int32 Index = 0; Index < Pages.Num(); ++Index)
    {
        if (Pages[Index]) Pages[Index]->SetVisibility(Index == SettingsPage ? ESlateVisibility::Visible : ESlateVisibility::Collapsed);
        if (SettingsTabs.IsValidIndex(Index)) SettingsTabs[Index]->SetStyle(ViewerUi::ButtonStyle(Index == SettingsPage
            ? FLinearColor(0.12f, 0.22f, 0.31f, 1.f) : FLinearColor(0.035f, 0.055f, 0.08f, 1.f), 6.f, FMargin(10.f, 13.f)));
    }
    if (SettingsScroll) SettingsScroll->ScrollToStart();
}
void UViewerWidget::OnGraphicsSettings() { SelectSettingsPage(0); }
void UViewerWidget::OnEffectsSettings() { SelectSettingsPage(1); }
void UViewerWidget::OnPartsSettings() { SelectSettingsPage(2); }
bool UViewerWidget::ConsumeModalPointer(FVector2D Point, bool bPressed)
{
    if (bConsumeDismissPointer)
    {
        if (!bPressed) bConsumeDismissPointer = false;
        return true;
    }
    if (!bSettingsOpen || bUiHidden) return false;
    if (bPressed && !ViewerUi::ContainsPixel(SettingsPanel, this, Point))
    {
        // A combo popup is a separate Slate window. Let its own hit testing
        // handle a menu choice outside the panel's rectangle.
        for (const UViewerSettingPicker* Picker : ChoicePickers)
            if (Picker && Picker->IsOpen()) return true;
        OnCloseSettings();
        bConsumeDismissPointer = true;
    }
    return true;
}
bool UViewerWidget::BlocksModelInput(FVector2D Point) const
{
    if (bSettingsOpen && !bUiHidden) return true;
    if (bUiHidden) return ViewerUi::ContainsPixel(RestoreControl, this, Point);
    if (OutfitPicker && OutfitPicker->IsOpen()) return true;
    return ViewerUi::ContainsPixel(CharacterRail, this, Point) || ViewerUi::ContainsPixel(ActionBar, this, Point) || ViewerUi::ContainsPixel(Footer, this, Point);
}
void UViewerWidget::SelectCharacterTile(int32 Index)
{
    if (!Viewer || Index < 0 || Index >= Viewer->GetCharacterCount()) return;
    Viewer->SelectCharacter(Index); Refresh();
}
void UViewerWidget::OnOutfitSelected(FString SelectedItem, ESelectInfo::Type SelectionType)
{
    if (!bRefreshing && Viewer && OutfitPicker->GetSelectedIndex() >= 0) { Viewer->SelectOutfit(OutfitPicker->GetSelectedIndex()); Refresh(); }
}
void UViewerWidget::OnPreviousOutfit() { if (Viewer) Viewer->ChangeOutfit(-1); }
void UViewerWidget::OnNextOutfit() { if (Viewer) Viewer->ChangeOutfit(1); }
void UViewerWidget::OnOpenSettings() { bSettingsOpen = !bSettingsOpen; Refresh(); }
void UViewerWidget::OnCloseSettings() { bSettingsOpen = false; bConsumeDismissPointer = true; Refresh(); }
void UViewerWidget::OnHideUi() { bSettingsOpen = false; bUiHidden = true; Refresh(); }
void UViewerWidget::OnShowUi() { bUiHidden = false; Refresh(); }
void UViewerWidget::OnReplay() { if (Viewer) Viewer->ReplayAnimation(); }
void UViewerWidget::OnResetView() { if (Viewer) Viewer->ResetView(); }
void UViewerWidget::OnRestoreDefaults() { if (Viewer) Viewer->ResetPreferences(); Refresh(); }
