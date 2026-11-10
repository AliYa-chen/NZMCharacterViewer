#include "PCAssetImportLibrary.h"

#include "PCSourceRecord.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"

bool UPCAssetImportLibrary::SetSourceRecordReferences(UPCSourceRecord* Record,const TArray<FString>& AssetPackages)
{
    if(!IsInGameThread() || !IsValid(Record) || !Record->GetPackage()->GetName().StartsWith(TEXT("/Game/PCAssets/")))
    {
        UE_LOG(LogTemp,Error,TEXT("PCAssetImporter: Source record must be a valid /Game/PCAssets asset on the editor game thread."));
        return false;
    }
    TArray<TSoftObjectPtr<UObject>> References;
    References.Reserve(AssetPackages.Num());
    TSet<FString> Seen;
    for(const FString& Package:AssetPackages)
    {
        if(!Package.StartsWith(TEXT("/Game/PCAssets/")) || Package.Contains(TEXT(".")) ||
           !FPackageName::IsValidLongPackageName(Package) || !FPackageName::DoesPackageExist(Package) || Seen.Contains(Package))
        {
            UE_LOG(LogTemp,Error,TEXT("PCAssetImporter: Soft-reference package is invalid, duplicated or absent on disk: %s"),*Package);
            return false;
        }
        Seen.Add(Package);
        const FString ObjectPath=Package+TEXT(".")+FPackageName::GetLongPackageAssetName(Package);
        References.Add(TSoftObjectPtr<UObject>(FSoftObjectPath(ObjectPath)));
    }
    // Validate every path before changing the record. Constructing a soft
    // pointer and checking the package file never loads the dependency.
    Record->Modify();
    Record->ImportedAssets=MoveTemp(References);
    return true;
}
