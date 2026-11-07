using UnrealBuildTool;
using System.Collections.Generic;

public class NZMCharacterViewerTarget : TargetRules
{
    public NZMCharacterViewerTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("NZMCharacterViewer");
    }
}
