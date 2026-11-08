#include "ViewerIOSViewport.h"

#if PLATFORM_IOS && !PLATFORM_TVOS && !PLATFORM_VISIONOS
#include "CoreMinimal.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "HAL/PlatformTime.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/ScopeLock.h"
#include "UnrealClient.h"
#include "IOS/IOSSystemIncludes.h"
#include "IOS/IOSAppDelegate.h"
#include "IOS/IOSApplication.h"
#include "IOS/IOSView.h"

namespace
{
    struct FNativeViewportSnapshot
    {
        uint64 Sequence = 0;
        double SampledAt = 0.0;
        UPTRINT ViewIdentity = 0;
        UPTRINT WindowIdentity = 0;
        int32 Orientation = 0;
        bool bActive = false;
        bool bTransitioning = false;
        bool bCanRecover = false;
        FVector2D SceneSize = FVector2D::ZeroVector;
        FVector2D WindowSize = FVector2D::ZeroVector;
        FVector2D ViewBounds = FVector2D::ZeroVector;
        FVector2D ViewFrame = FVector2D::ZeroVector;
        FVector2D ViewSize = FVector2D::ZeroVector;
        FVector2D DrawableSize = FVector2D::ZeroVector;
        FIntPoint RequestedUEViewport = FIntPoint::ZeroValue;

        bool SameNativeGeometry(const FNativeViewportSnapshot& Other) const
        {
            return ViewIdentity == Other.ViewIdentity && WindowIdentity == Other.WindowIdentity
                && Orientation == Other.Orientation && SceneSize.Equals(Other.SceneSize, 0.5)
                && WindowSize.Equals(Other.WindowSize, 0.5) && ViewBounds.Equals(Other.ViewBounds, 0.5)
                && ViewFrame.Equals(Other.ViewFrame, 0.5);
        }
    };

    FCriticalSection SnapshotMutex;
    FNativeViewportSnapshot LatestSnapshot;
    bool bSnapshotPending = false;
    uint64 LastRecoveryAcknowledged = 0;
    bool bLastRecoveryExecuted = false;

    FVector2D Size(CGSize Value) { return FVector2D(Value.width, Value.height); }

    bool ValidSize(const FVector2D& Value)
    {
        return FMath::IsFinite(Value.X) && FMath::IsFinite(Value.Y) && Value.GetMin() > 0.0;
    }

    bool MatchesOrientation(const FVector2D& Value, UIInterfaceOrientation Orientation)
    {
        if (!ValidSize(Value)) return false;
        if (UIInterfaceOrientationIsLandscape(Orientation)) return Value.X > Value.Y;
        if (UIInterfaceOrientationIsPortrait(Orientation)) return Value.Y > Value.X;
        return false;
    }

    FNativeViewportSnapshot SampleNativeViewport(uint64 Sequence, FIntPoint UEViewport)
    {
        check([NSThread isMainThread]);
        FNativeViewportSnapshot Result;
        Result.Sequence = Sequence;
        Result.SampledAt = FPlatformTime::Seconds();
        Result.RequestedUEViewport = UEViewport;
        IOSAppDelegate* Delegate = [IOSAppDelegate GetDelegate];
        FIOSView* View = Delegate.IOSView;
        UIWindow* Window = View.window;
        UIWindowScene* Scene = Window.windowScene;
        if (!View || !Window || !Scene) return Result;
        // Follow the actual UE view's window/scene. The device's physical pose
        // (including faceDown) and the application's first screen are not used.
        UIInterfaceOrientation Orientation = UIInterfaceOrientationUnknown;
        id<UICoordinateSpace> CoordinateSpace = nil;
        if (@available(iOS 26.0, *))
        {
            Orientation = Scene.effectiveGeometry.interfaceOrientation;
            CoordinateSpace = Scene.effectiveGeometry.coordinateSpace;
        }
        else
        {
            Orientation = Scene.interfaceOrientation;
            CoordinateSpace = Scene.coordinateSpace;
        }
        Result.ViewIdentity = reinterpret_cast<UPTRINT>((__bridge void*)View);
        Result.WindowIdentity = reinterpret_cast<UPTRINT>((__bridge void*)Window);
        Result.Orientation = static_cast<int32>(Orientation);
        Result.SceneSize = Size(CoordinateSpace.bounds.size);
        Result.WindowSize = Size(Window.bounds.size);
        Result.ViewBounds = Size(View.bounds.size);
        Result.ViewFrame = Size(View.frame.size);
        Result.ViewSize = Size(View.ViewSize);
        CAMetalLayer* MetalLayer = View->CachedMetalLayer;
        if (!MetalLayer && [View.layer isKindOfClass:[CAMetalLayer class]]) MetalLayer = (CAMetalLayer*)View.layer;
        if (MetalLayer) Result.DrawableSize = Size(MetalLayer.drawableSize);
        Result.bActive = Scene.activationState == UISceneActivationStateForegroundActive
            && [UIApplication sharedApplication].applicationState == UIApplicationStateActive;
        Result.bTransitioning = Window.rootViewController.transitionCoordinator != nil
            || Delegate.IOSController.transitionCoordinator != nil;
        Result.bCanRecover = Result.bActive && !Result.bTransitioning && View->bIsInitialized
            && !Window.hidden && !View.hidden && View.alpha > 0.f
            && MatchesOrientation(Result.SceneSize, Orientation)
            && MatchesOrientation(Result.WindowSize, Orientation)
            && MatchesOrientation(Result.ViewBounds, Orientation);
        return Result;
    }

    void RequestSnapshot(uint64 Sequence, FIntPoint UEViewport)
    {
        dispatch_async(dispatch_get_main_queue(), ^
        {
            @autoreleasepool
            {
                const FNativeViewportSnapshot Snapshot = SampleNativeViewport(Sequence, UEViewport);
                FScopeLock Lock(&SnapshotMutex);
                LatestSnapshot = Snapshot;
                bSnapshotPending = false;
            }
        });
    }
}

void ViewerIOSViewport::Tick()
{
    if (!IsInGameThread()) return;
    const double Now = FPlatformTime::Seconds();
    static double LastRequestAt = -1.0;
    static uint64 NextSequence = 0;
    static uint64 ProcessedSequence = 0;
    static FNativeViewportSnapshot StableGeometry;
    static double GeometryStableSince = 0.0;
    static double MismatchSince = -1.0;
    static bool bAttemptedForGeometry = false;
    static double LastRecoveryAt = -2.0;
    static uint64 LastRequestedRecoverySequence = 0;
    static FNativeViewportSnapshot LastLogged;
    static FIntPoint LastLoggedUEViewport = FIntPoint::ZeroValue;
    static const bool bObserveOnly = FParse::Param(FCommandLine::Get(), TEXT("ViewerViewportObserveOnly"));
    const FIntPoint UEViewport = GEngine && GEngine->GameViewport && GEngine->GameViewport->Viewport
        ? GEngine->GameViewport->Viewport->GetSizeXY() : FIntPoint::ZeroValue;

    FNativeViewportSnapshot Snapshot;
    {
        FScopeLock Lock(&SnapshotMutex);
        Snapshot = LatestSnapshot;
        if (LastRequestedRecoverySequence && LastRecoveryAcknowledged == LastRequestedRecoverySequence)
        {
            if (!bLastRecoveryExecuted) bAttemptedForGeometry = false;
            LastRequestedRecoverySequence = 0;
        }
        if (Now - LastRequestAt >= 0.2 && !bSnapshotPending)
        {
            bSnapshotPending = true;
            LastRequestAt = Now;
            RequestSnapshot(++NextSequence, UEViewport);
        }
    }
    if (!Snapshot.Sequence || Snapshot.Sequence == ProcessedSequence) return;
    ProcessedSequence = Snapshot.Sequence;
    if (!Snapshot.SameNativeGeometry(StableGeometry))
    {
        StableGeometry = Snapshot;
        GeometryStableSince = Now;
        MismatchSince = -1.0;
        bAttemptedForGeometry = false;
    }
    const bool bGeometryValid = ValidSize(Snapshot.ViewBounds) && UEViewport.GetMin() > 0;
    const double NativeAspect = bGeometryValid ? Snapshot.ViewBounds.X / Snapshot.ViewBounds.Y : 0.0;
    const double UEAspect = bGeometryValid ? static_cast<double>(UEViewport.X) / UEViewport.Y : 0.0;
    const double AspectError = bGeometryValid ? FMath::Abs(UEAspect / NativeAspect - 1.0) : 0.0;
    const bool bMismatch = Snapshot.bCanRecover && Now - Snapshot.SampledAt < 0.75 && bGeometryValid && AspectError > 0.02;
    if (bMismatch) { if (MismatchSince < 0.0) MismatchSince = Now; }
    else
    {
        MismatchSince = -1.0;
        // A resolved mismatch or a lifecycle/transition interval ends this
        // episode. A later mismatch at the same geometry may need recovery.
        bAttemptedForGeometry = false;
    }

    if (!Snapshot.SameNativeGeometry(LastLogged) || !Snapshot.ViewSize.Equals(LastLogged.ViewSize, 0.5)
        || !Snapshot.DrawableSize.Equals(LastLogged.DrawableSize, 0.5) || UEViewport != LastLoggedUEViewport
        || Snapshot.bActive != LastLogged.bActive || Snapshot.bTransitioning != LastLogged.bTransitioning)
    {
        UE_LOG(LogTemp, Display, TEXT("Viewer iOS viewport sample=%llu interface=%d active=%d transition=%d scene=%.1fx%.1f window=%.1fx%.1f bounds=%.1fx%.1f frame=%.1fx%.1f ViewSize=%.1fx%.1f drawable=%.1fx%.1f UE(request)=%dx%d UE(now)=%dx%d aspectError=%.4f observeOnly=%d"),
            Snapshot.Sequence, Snapshot.Orientation, Snapshot.bActive, Snapshot.bTransitioning,
            Snapshot.SceneSize.X, Snapshot.SceneSize.Y, Snapshot.WindowSize.X, Snapshot.WindowSize.Y,
            Snapshot.ViewBounds.X, Snapshot.ViewBounds.Y, Snapshot.ViewFrame.X, Snapshot.ViewFrame.Y,
            Snapshot.ViewSize.X, Snapshot.ViewSize.Y, Snapshot.DrawableSize.X, Snapshot.DrawableSize.Y,
            Snapshot.RequestedUEViewport.X, Snapshot.RequestedUEViewport.Y, UEViewport.X, UEViewport.Y, AspectError, bObserveOnly);
        LastLogged = Snapshot;
        LastLoggedUEViewport = UEViewport;
    }
    if (bObserveOnly || !bMismatch || bAttemptedForGeometry || MismatchSince < 0.0
        || Now - MismatchSince < 0.5 || Now - GeometryStableSince < 0.5 || Now - LastRecoveryAt < 2.0) return;
    bAttemptedForGeometry = true;
    LastRecoveryAt = Now;
    LastRequestedRecoverySequence = Snapshot.Sequence;
    const FNativeViewportSnapshot Expected = Snapshot;
    dispatch_async(dispatch_get_main_queue(), ^
    {
        @autoreleasepool
        {
            const FNativeViewportSnapshot Current = SampleNativeViewport(Expected.Sequence, Expected.RequestedUEViewport);
            const bool bExecute = Current.bCanRecover && Current.SameNativeGeometry(Expected);
            if (bExecute)
            {
                IOSAppDelegate* Delegate = [IOSAppDelegate GetDelegate];
                // Ask the existing engine path to reconsider its layout/orientation.
                // Never set a Metal drawable size or impose a guessed resolution.
                [Delegate.IOSView forceLayoutSubviews];
                FIOSApplication::OrientationChanged(static_cast<UIInterfaceOrientation>(Current.Orientation));
                UE_LOG(LogTemp, Warning, TEXT("Viewer iOS viewport recovery requested sample=%llu interface=%d nativeBounds=%.1fx%.1f UE=%dx%d; engine layout/orientation path only"),
                    Expected.Sequence, Current.Orientation, Current.ViewBounds.X, Current.ViewBounds.Y,
                    Expected.RequestedUEViewport.X, Expected.RequestedUEViewport.Y);
            }
            FScopeLock Lock(&SnapshotMutex);
            LastRecoveryAcknowledged = Expected.Sequence;
            bLastRecoveryExecuted = bExecute;
        }
    });
}

#else
void ViewerIOSViewport::Tick() {}
#endif
