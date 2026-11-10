#include "PCAssetImportLibrary.h"

#include "DataDrivenShaderPlatformInfo.h"
#include "Dom/JsonObject.h"
#include "DynamicRHI.h"
#include "HAL/PlatformTime.h"
#include "Materials/Material.h"
#include "Materials/MaterialInterface.h"
#include "MaterialShared.h"
#include "Misc/PackageName.h"
#include "RHI.h"
#include "RHIStrings.h"
#include "RenderingThread.h"
#include "Serialization/JsonSerializer.h"
#include "ShaderCompiler.h"
#include "UObject/Package.h"
#include "UObject/StrongObjectPtr.h"

namespace PCMaterialShaders
{
static FString Encode(const TSharedRef<FJsonObject>& Object)
{
    FString Text;
    FJsonSerializer::Serialize(Object,TJsonWriterFactory<>::Create(&Text));
    return Text;
}

static bool Allowed(const FString& Package)
{
    return (Package.StartsWith(TEXT("/Game/PCAssets/Materials/")) || Package.StartsWith(TEXT("/Game/PCAssets/ViewerMaterials/")) ||
        Package.StartsWith(TEXT("/Game/PCAssets/ViewerShading/"))) &&
        FPackageName::IsValidLongPackageName(Package) && !Package.Contains(TEXT("."));
}

struct FEntry
{
    FString Package;
    TStrongObjectPtr<UMaterialInterface> Asset;
    TSharedRef<FJsonObject> Result=MakeShared<FJsonObject>();
};
}

FString UPCAssetImportLibrary::VerifyMaterialShaders(const TArray<FString>& MaterialPackages)
{
    using namespace PCMaterialShaders;
    const double Started=FPlatformTime::Seconds();
    const TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
    Report->SetNumberField(TEXT("schema_version"),1);
    Report->SetStringField(TEXT("status"),TEXT("not_verified"));
    Report->SetBoolField(TEXT("success"),false);
    Report->SetNumberField(TEXT("requested_count"),MaterialPackages.Num());
    Report->SetBoolField(TEXT("content_saved"),false);
    Report->SetBoolField(TEXT("forced_recompile"),false);
    Report->SetStringField(TEXT("scope"),TEXT("Active Metal RHI material resources: shader map presence, exact platform, completed successful compilation, complete required shader set, render-valid frozen content and no compiler errors. Shared parent resources are recorded. This does not establish original shader equivalence, image correctness, PSO execution or device performance."));
    const FString RHIName=GDynamicRHI?FString(GDynamicRHI->GetName()):TEXT("Uninitialized");
    Report->SetStringField(TEXT("rhi"),RHIName);
    Report->SetBoolField(TEXT("using_null_rhi"),GUsingNullRHI);
    const bool ValidPlatform=GMaxRHIShaderPlatform!=SP_NumPlatforms && FDataDrivenShaderPlatformInfo::IsValid(GMaxRHIShaderPlatform);
    Report->SetStringField(TEXT("shader_platform"),ValidPlatform?FDataDrivenShaderPlatformInfo::GetName(GMaxRHIShaderPlatform).ToString():TEXT("Uninitialized"));
    Report->SetStringField(TEXT("shader_format"),ValidPlatform?LegacyShaderPlatformToShaderFormat(GMaxRHIShaderPlatform).ToString():TEXT("Uninitialized"));
    FString FeatureLevel;
    if(GMaxRHIFeatureLevel<ERHIFeatureLevel::Num) GetFeatureLevelName(GMaxRHIFeatureLevel,FeatureLevel);
    else FeatureLevel=TEXT("Uninitialized");
    Report->SetStringField(TEXT("feature_level"),FeatureLevel);
    if(!IsInGameThread() || !GDynamicRHI || GUsingNullRHI || RHIName.Contains(TEXT("Null"),ESearchCase::IgnoreCase) ||
       !ValidPlatform || !IsMetalPlatform(GMaxRHIShaderPlatform) || !RHIName.Contains(TEXT("Metal"),ESearchCase::IgnoreCase))
    {
        Report->SetStringField(TEXT("reason"),TEXT("Verification requires the editor game thread and an initialized real Metal RHI/shader platform; NullRHI and offline structural checks cannot verify shaders."));
        return Encode(Report);
    }
    if(MaterialPackages.IsEmpty())
    {
        Report->SetStringField(TEXT("reason"),TEXT("No material packages were requested."));
        return Encode(Report);
    }

    const int32 JobsBefore=GShaderCompilingManager?GShaderCompilingManager->GetNumRemainingJobs():0;
    Report->SetNumberField(TEXT("shader_jobs_before"),JobsBefore);
    TArray<FEntry> Entries;
    Entries.Reserve(MaterialPackages.Num());
    TSet<FString> Seen;
    TSet<FMaterialResource*> Prepared;
    int32 SubmittedResources=0;
    for(const FString& Package:MaterialPackages)
    {
        FEntry& Entry=Entries.AddDefaulted_GetRef();
        Entry.Package=Package;
        Entry.Result->SetStringField(TEXT("package"),Package);
        Entry.Result->SetStringField(TEXT("status"),TEXT("failed"));
        Entry.Result->SetBoolField(TEXT("cache_requested"),false);
        Entry.Result->SetBoolField(TEXT("compile_jobs_submitted"),false);
        if(!Allowed(Package) || Seen.Contains(Package))
        {
            Entry.Result->SetStringField(TEXT("error"),TEXT("Package is outside the PC Materials/ViewerMaterials/ViewerShading namespaces, invalid, or duplicated."));
            continue;
        }
        Seen.Add(Package);
        if(!FPackageName::DoesPackageExist(Package))
        {
            Entry.Result->SetStringField(TEXT("error"),TEXT("Material package does not exist on disk."));
            continue;
        }
        const FString ObjectPath=Package+TEXT(".")+FPackageName::GetLongPackageAssetName(Package);
        UMaterialInterface* Material=LoadObject<UMaterialInterface>(nullptr,*ObjectPath);
        if(!Material || Material->GetPathName()!=ObjectPath)
        {
            Entry.Result->SetStringField(TEXT("error"),TEXT("Exact saved MaterialInterface did not load."));
            continue;
        }
        Entry.Asset=TStrongObjectPtr<UMaterialInterface>(Material);
        Entry.Result->SetStringField(TEXT("class"),Material->GetClass()->GetName());
        const UMaterial* Base=Material->GetMaterial();
        Entry.Result->SetStringField(TEXT("base_material"),Base?Base->GetPathName():TEXT("None"));
        if(!Base || !Allowed(Base->GetPackage()->GetName()))
        {
            Entry.Result->SetStringField(TEXT("error"),TEXT("Base material is missing or resolves outside the approved PC material namespaces; an Engine default fallback cannot pass."));
            continue;
        }
        FMaterialResource* Resource=Material->GetMaterialResource(GMaxRHIShaderPlatform);
        if(!Resource || Resource->GetShaderPlatform()!=GMaxRHIShaderPlatform)
        {
            Entry.Result->SetStringField(TEXT("error"),TEXT("No material resource for the active shader platform."));
            continue;
        }
        if(Prepared.Contains(Resource)) continue;
        Prepared.Add(Resource);
        // Loading/PostLoad normally starts the correct cached resource. Finish
        // pending DDC work first; request a cache only when no shader map or
        // compilation is present, without changing the material's shader ID.
        Resource->FinishCacheShaders();
        if(!Resource->GetGameThreadShaderMap() && Resource->GetCompileErrors().IsEmpty())
        {
            Resource->CacheShaders();
            Resource->FinishCacheShaders();
            Entry.Result->SetBoolField(TEXT("cache_requested"),true);
        }
        if(!Resource->IsGameThreadShaderMapComplete() && Resource->GetCompileErrors().IsEmpty())
        {
            Resource->SubmitCompileJobs_GameThread(EShaderCompileJobPriority::High);
            Entry.Result->SetBoolField(TEXT("compile_jobs_submitted"),true);
            ++SubmittedResources;
        }
    }
    if(GShaderCompilingManager) GShaderCompilingManager->FinishAllCompilation();
    // Some material resources may finish asynchronous cache callbacks after
    // the global wait. Finish their own jobs as well, then publish to RHI.
    for(FMaterialResource* Resource:Prepared) Resource->FinishCompilation();
    FlushRenderingCommands();

    TArray<TSharedPtr<FJsonValue>> Results;
    int32 Passed=0;
    for(FEntry& Entry:Entries)
    {
        UMaterialInterface* Material=Entry.Asset.Get();
        if(Material && !Entry.Result->HasField(TEXT("error")))
        {
            FMaterialResource* Resource=Material->GetMaterialResource(GMaxRHIShaderPlatform);
            FMaterialShaderMap* ShaderMap=Resource?Resource->GetGameThreadShaderMap():nullptr;
            UMaterialInterface* ResourceOwner=Resource?Resource->GetMaterialInterface():nullptr;
            TArray<TSharedPtr<FJsonValue>> Errors;
            if(Resource)
                for(const FString& Message:Resource->GetCompileErrors()) Errors.Add(MakeShared<FJsonValueString>(Message));
            const bool CorrectOwner=ResourceOwner && Allowed(ResourceOwner->GetPackage()->GetName());
            const bool CorrectPlatform=Resource && ShaderMap && Resource->GetShaderPlatform()==GMaxRHIShaderPlatform && ShaderMap->GetShaderPlatform()==GMaxRHIShaderPlatform;
            const bool Finalized=ShaderMap && ShaderMap->IsCompilationFinalized();
            const bool Compiled=ShaderMap && ShaderMap->CompiledSuccessfully();
            // The exported cached completeness accessor is calculated by
            // FMaterial::SetGameThreadShaderMap via ShaderMap->IsComplete(this,
            // true). Calling the latter directly is not exported in this UE.
            const bool Complete=Resource && Resource->IsGameThreadShaderMapComplete();
            const bool Valid=ShaderMap && ShaderMap->IsValidForRendering();
            const uint32 ShaderCount=ShaderMap?ShaderMap->GetShaderNum():0;
            const bool Success=CorrectOwner && CorrectPlatform && Finalized && Compiled && Complete && Valid && Errors.IsEmpty() && ShaderCount>0;
            Entry.Result->SetBoolField(TEXT("shader_map_present"),ShaderMap!=nullptr);
            Entry.Result->SetBoolField(TEXT("platform_matches_active_rhi"),CorrectPlatform);
            Entry.Result->SetBoolField(TEXT("compilation_finalized"),Finalized);
            Entry.Result->SetBoolField(TEXT("compiled_successfully"),Compiled);
            Entry.Result->SetBoolField(TEXT("required_shader_set_complete"),Complete);
            Entry.Result->SetBoolField(TEXT("valid_for_rendering"),Valid);
            Entry.Result->SetNumberField(TEXT("shader_count"),ShaderCount);
            Entry.Result->SetArrayField(TEXT("compile_errors"),Errors);
            Entry.Result->SetStringField(TEXT("resource_owner"),ResourceOwner?ResourceOwner->GetPathName():TEXT("None"));
            Entry.Result->SetBoolField(TEXT("shares_parent_resource"),ResourceOwner && ResourceOwner!=Material);
            Entry.Result->SetStringField(TEXT("status"),Success?TEXT("passed"):TEXT("failed"));
            if(Success) ++Passed;
            else Entry.Result->SetStringField(TEXT("error"),TEXT("An active-RHI material shader map requirement failed; see individual flags and compiler errors."));
        }
        Results.Add(MakeShared<FJsonValueObject>(Entry.Result));
    }
    Report->SetStringField(TEXT("status"),Passed==Entries.Num()?TEXT("passed"):TEXT("failed"));
    Report->SetBoolField(TEXT("success"),Passed==Entries.Num());
    Report->SetNumberField(TEXT("passed_count"),Passed);
    Report->SetNumberField(TEXT("failed_count"),Entries.Num()-Passed);
    Report->SetNumberField(TEXT("checked_count"),Entries.Num());
    Report->SetNumberField(TEXT("unique_material_resources_checked"),Prepared.Num());
    Report->SetNumberField(TEXT("resources_with_compile_jobs_submitted"),SubmittedResources);
    Report->SetNumberField(TEXT("shader_jobs_after"),GShaderCompilingManager?GShaderCompilingManager->GetNumRemainingJobs():0);
    Report->SetNumberField(TEXT("elapsed_seconds"),FPlatformTime::Seconds()-Started);
    Report->SetArrayField(TEXT("materials"),Results);
    return Encode(Report);
}
