using UnrealBuildTool;
using System.IO;

public class NZMCharacterViewer : ModuleRules
{
    public NZMCharacterViewer(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore", "UMG" });
        PrivateDependencyModuleNames.AddRange(new string[] { "Slate", "SlateCore", "Json", "ApplicationCore", "RenderCore", "RHI", "ClothingSystemRuntimeInterface", "ClothingSystemRuntimeCommon" });
        if (Target.Platform == UnrealTargetPlatform.IOS || Target.Platform == UnrealTargetPlatform.Mac)
        {
            // Native capability reporting uses public MTLDevice queries.
            PublicWeakFrameworks.Add("Metal");
        }
        if (Target.Platform == UnrealTargetPlatform.IOS)
        {
            // Allow the scene to fill foldable displays and resize with their active screen.
            AdditionalPropertiesForReceipt.Add("IOSPlugin", Path.Combine(ModuleDirectory, "ViewerIOS_UPL.xml"));
        }
    }
}
