#include "ViewerStyle.h"
#include "Fonts/CompositeFont.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "Styling/CoreStyle.h"

FSlateFontInfo ViewerStyle::Font(int32 Size)
{
    static TSharedPtr<const FCompositeFont> Composite = []() -> TSharedPtr<const FCompositeFont>
    {
        const FString Path = FPaths::ProjectContentDir() / TEXT("Slate/Fonts/NotoSansCJKsc-Regular.otf");
        if (!IFileManager::Get().FileExists(*Path))
        {
            UE_LOG(LogTemp, Error, TEXT("Bundled Chinese font missing: %s"), *Path);
            return nullptr;
        }
        TSharedPtr<FCompositeFont> Result = MakeShared<FCompositeFont>();
        Result->DefaultTypeface.Fonts.Add(FTypefaceEntry(FName(TEXT("Regular")),
            Path, EFontHinting::Default, EFontLoadingPolicy::LazyLoad));
        return Result;
    }();
    return Composite.IsValid()
        ? FSlateFontInfo(Composite, static_cast<float>(Size), FName(TEXT("Regular")), FFontOutlineSettings())
        : FCoreStyle::GetDefaultFontStyle("Regular", Size);
}
