#include "ViewerGraphicsCapabilities.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#if PLATFORM_IOS || PLATFORM_MAC
#if PLATFORM_MAC
// This includes Foundation with UE's Carbon FVector name workaround.
#include "Mac/MacSystemIncludes.h"
#endif
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#endif

namespace
{
    struct FNativeGraphicsCapabilities
    {
        bool bDeviceAvailable = false;
        bool bRayTracingQueryAvailable = false;
        bool bRayTracing = false;
        bool bWorkingSetQueryAvailable = false;
        uint64 RecommendedWorkingSetBytes = 0;
        FString GPUName;
        TArray<int32> SampleCounts;
    };

    const FNativeGraphicsCapabilities& NativeCapabilities()
    {
        // Device capabilities are immutable for this Apple viewer process.
        // C++ local-static initialization is thread safe; all Objective-C
        // objects are confined to the pool and copied into native UE values.
        static const FNativeGraphicsCapabilities Result = []()
        {
            FNativeGraphicsCapabilities Value;
#if PLATFORM_IOS || PLATFORM_MAC
            @autoreleasepool
            {
                // MTLCreateSystemDefaultDevice follows the Create ownership
                // rule. Release explicitly under MRC, or let ARC own it.
                id<MTLDevice> Device = MTLCreateSystemDefaultDevice();
                if (Device)
                {
                    Value.bDeviceAvailable = true;
                    const char* Name = [[Device name] UTF8String];
                    if (Name) Value.GPUName = UTF8_TO_TCHAR(Name);
                    for (int32 Samples : { 1, 2, 4, 8 })
                        if ([Device supportsTextureSampleCount:static_cast<NSUInteger>(Samples)])
                            Value.SampleCounts.Add(Samples);

                    if (@available(macOS 11.0, iOS 14.0, *))
                    {
                        Value.bRayTracingQueryAvailable = true;
                        Value.bRayTracing = [Device supportsRaytracing];
                    }
                    if (@available(macOS 10.12, iOS 16.0, *))
                    {
                        Value.bWorkingSetQueryAvailable = true;
                        Value.RecommendedWorkingSetBytes = [Device recommendedMaxWorkingSetSize];
                    }
#if !__has_feature(objc_arc)
                    [Device release];
#endif
                }
            }
#endif
            return Value;
        }();
        return Result;
    }
}

FString UViewerGraphicsCapabilities::ReportJson()
{
    const FNativeGraphicsCapabilities& Native = NativeCapabilities();
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("source"), TEXT("MTLCreateSystemDefaultDevice public Metal API"));
    Report->SetStringField(TEXT("device_scope"), TEXT("system_default_metal_device; an engine-selected external GPU may differ on macOS"));
    Report->SetBoolField(TEXT("device_available"), Native.bDeviceAvailable);
    Report->SetStringField(TEXT("native_gpu_name"), Native.GPUName);
    Report->SetBoolField(TEXT("ray_tracing_query_available"), Native.bRayTracingQueryAvailable);
    if (Native.bRayTracingQueryAvailable) Report->SetBoolField(TEXT("supports_ray_tracing"), Native.bRayTracing);
    else Report->SetField(TEXT("supports_ray_tracing"), MakeShared<FJsonValueNull>());
    TArray<TSharedPtr<FJsonValue>> SampleCounts;
    for (int32 Samples : Native.SampleCounts) SampleCounts.Add(MakeShared<FJsonValueNumber>(Samples));
    Report->SetArrayField(TEXT("supported_msaa_sample_counts"), SampleCounts);
    Report->SetNumberField(TEXT("max_msaa_samples"), MaxMSAASamples());
    Report->SetBoolField(TEXT("recommended_working_set_query_available"), Native.bWorkingSetQueryAvailable);
    if (Native.bWorkingSetQueryAvailable)
        Report->SetStringField(TEXT("recommended_max_working_set_bytes"), LexToString(Native.RecommendedWorkingSetBytes));
    else Report->SetField(TEXT("recommended_max_working_set_bytes"), MakeShared<FJsonValueNull>());
    // The installed public MTLDevice API has no maxTextureDimension2D query.
    // Report unknown rather than inferring a limit from a model/family table
    // or probing allocations that could consume device memory.
    Report->SetField(TEXT("native_max_texture_dimension_2d"), MakeShared<FJsonValueNull>());
    Report->SetStringField(TEXT("texture_dimension_query"), TEXT("not exposed by the public MTLDevice API in the installed SDK"));
    Report->SetStringField(TEXT("capability_scope"), TEXT("Native device capability only; does not establish Unreal RHI/shader/format support or enable Lumen, hardware ray tracing, or path tracing."));
    FString Json;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json));
    return Json;
}

FString UViewerGraphicsCapabilities::NativeGPUName()
{
    return NativeCapabilities().GPUName;
}

bool UViewerGraphicsCapabilities::SupportsRayTracing()
{
    const auto& Native = NativeCapabilities();
    return Native.bRayTracingQueryAvailable && Native.bRayTracing;
}

TArray<int32> UViewerGraphicsCapabilities::SupportedMSAASampleCounts()
{
    return NativeCapabilities().SampleCounts;
}

int32 UViewerGraphicsCapabilities::MaxMSAASamples()
{
    const TArray<int32>& Samples = NativeCapabilities().SampleCounts;
    return Samples.IsEmpty() ? 0 : Samples.Last();
}
