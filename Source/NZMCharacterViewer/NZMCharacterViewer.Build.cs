using UnrealBuildTool;

public class NZMCharacterViewer : ModuleRules
{
    public NZMCharacterViewer(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore", "UMG" });
        PrivateDependencyModuleNames.AddRange(new string[] { "Slate", "SlateCore", "Json", "ApplicationCore", "RenderCore" });
    }
}
