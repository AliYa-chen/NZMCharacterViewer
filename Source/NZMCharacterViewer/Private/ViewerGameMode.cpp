#include "ViewerGameMode.h"
#include "ViewerPawn.h"
#include "GameFramework/PlayerController.h"

AViewerGameMode::AViewerGameMode()
{
    DefaultPawnClass = AViewerPawn::StaticClass();
    PlayerControllerClass = APlayerController::StaticClass();
}
