using UnrealBuildTool;

public class PCAssetImporter : ModuleRules
{
    public PCAssetImporter(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] {
            "PCAssetData", "UnrealEd", "AssetRegistry", "Json", "JsonUtilities",
            "MeshBuilder", "TargetPlatform", "MeshDescription", "StaticMeshDescription", "SkeletalMeshDescription",
            "AnimationCore", "RenderCore", "RHI", "MovieScene", "MovieSceneTracks", "ControlRig",
            "ClothingSystemRuntimeCommon", "ClothingSystemRuntimeInterface", "ChaosCloth", "PhysicsCore", "PhysicsUtilities"
        });
    }
}
