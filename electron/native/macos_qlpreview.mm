/** macOS 把 QLPreviewView 嵌进 Electron 窗口，而不是弹出系统 Quick Look 面板。 */

#include <string>
#include <napi.h>
#import <Cocoa/Cocoa.h>
#import <Quartz/Quartz.h>

@interface YinliPreviewItem : NSObject <QLPreviewItem>
@property (strong) NSURL *previewItemURL;
@property (copy) NSString *titleValue;
@end

@implementation YinliPreviewItem
- (NSString *)previewItemTitle {
  return self.titleValue;
}
@end

@interface YinliPreviewPanel : NSPanel
@end

@implementation YinliPreviewPanel
- (BOOL)canBecomeKeyWindow {
  return YES;
}
- (BOOL)canBecomeMainWindow {
  return NO;
}
@end

static NSWindow *gChild = nil;
static QLPreviewView *gPreview = nil;
static YinliPreviewItem *gItem = nil;

static NSWindow *FindElectronWindow(void) {
  NSWindow *key = [NSApp keyWindow];
  if (key && key.isVisible && key.parentWindow == nil) {
    return key;
  }
  for (NSWindow *window in [NSApp windows]) {
    if (!window.isVisible || window.parentWindow != nil) {
      continue;
    }
    if (window.styleMask & NSWindowStyleMaskTitled || window.styleMask & NSWindowStyleMaskFullSizeContentView) {
      return window;
    }
  }
  return [NSApp mainWindow];
}

static NSRect ScreenFrameFromContentRect(NSWindow *parent, double x, double y, double width, double height) {
  NSView *content = parent.contentView;
  CGFloat flippedY = content.isFlipped ? y : (NSHeight(content.bounds) - y - height);
  NSRect viewRect = NSMakeRect(x, flippedY, width, height);
  NSRect windowRect = [content convertRect:viewRect toView:nil];
  return [parent convertRectToScreen:windowRect];
}

static void CloseEmbeddedPreview(void) {
  if (gPreview) {
    [gPreview close];
    [gPreview removeFromSuperview];
    gPreview = nil;
  }
  if (gChild) {
    NSWindow *parent = gChild.parentWindow;
    if (parent) {
      [parent removeChildWindow:gChild];
    }
    [gChild close];
    gChild = nil;
  }
  gItem = nil;
}

static int ShowEmbeddedPreview(const std::string &path, double x, double y, double width, double height) {
  if (path.empty() || width < 8 || height < 8) {
    return -1;
  }
  NSWindow *parent = FindElectronWindow();
  if (!parent) {
    return -2;
  }
  NSString *pathString = [NSString stringWithUTF8String:path.c_str()];
  if (pathString.length == 0 || ![[NSFileManager defaultManager] fileExistsAtPath:pathString]) {
    return -3;
  }
  NSRect screenRect = ScreenFrameFromContentRect(parent, x, y, width, height);
  if (!gChild) {
    YinliPreviewPanel *panel = [[YinliPreviewPanel alloc] initWithContentRect:screenRect
                                                                   styleMask:NSWindowStyleMaskBorderless
                                                                     backing:NSBackingStoreBuffered
                                                                       defer:NO];
    panel.releasedWhenClosed = NO;
    panel.opaque = YES;
    panel.hasShadow = NO;
    panel.hidesOnDeactivate = NO;
    panel.becomesKeyOnlyIfNeeded = YES;
    panel.floatingPanel = NO;
    panel.acceptsMouseMovedEvents = YES;
    panel.animationBehavior = NSWindowAnimationBehaviorNone;
    panel.backgroundColor = [NSColor colorWithCalibratedWhite:0.91 alpha:1.0];
    panel.collectionBehavior = NSWindowCollectionBehaviorFullScreenAuxiliary;
    gChild = panel;
    gPreview = [[QLPreviewView alloc] initWithFrame:gChild.contentView.bounds style:QLPreviewViewStyleNormal];
    gPreview.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    gPreview.autostarts = YES;
    gPreview.shouldCloseWithWindow = NO;
    [gChild.contentView addSubview:gPreview];
    [parent addChildWindow:gChild ordered:NSWindowAbove];
  } else {
    if (gChild.parentWindow != parent) {
      [gChild.parentWindow removeChildWindow:gChild];
      [parent addChildWindow:gChild ordered:NSWindowAbove];
    }
    [gChild setFrame:screenRect display:YES];
  }
  gItem = [YinliPreviewItem new];
  gItem.previewItemURL = [NSURL fileURLWithPath:pathString];
  gItem.titleValue = pathString.lastPathComponent;
  gPreview.previewItem = gItem;
  [gPreview refreshPreviewItem];
  [gChild makeKeyAndOrderFront:nil];
  return 0;
}

static Napi::Value Show(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 5 || !info[0].IsString()) {
    Napi::TypeError::New(env, "show(path, x, y, width, height)").ThrowAsJavaScriptException();
    return env.Null();
  }
  std::string path = info[0].As<Napi::String>().Utf8Value();
  double x = info[1].As<Napi::Number>().DoubleValue();
  double y = info[2].As<Napi::Number>().DoubleValue();
  double width = info[3].As<Napi::Number>().DoubleValue();
  double height = info[4].As<Napi::Number>().DoubleValue();
  int code = ShowEmbeddedPreview(path, x, y, width, height);
  return Napi::Number::New(env, code);
}

static Napi::Value Update(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (!gChild || info.Length() < 4) {
    return Napi::Number::New(env, -1);
  }
  NSWindow *parent = gChild.parentWindow ?: FindElectronWindow();
  if (!parent) {
    return Napi::Number::New(env, -2);
  }
  NSRect screenRect = ScreenFrameFromContentRect(
    parent,
    info[0].As<Napi::Number>().DoubleValue(),
    info[1].As<Napi::Number>().DoubleValue(),
    info[2].As<Napi::Number>().DoubleValue(),
    info[3].As<Napi::Number>().DoubleValue()
  );
  [gChild setFrame:screenRect display:YES];
  return Napi::Number::New(env, 0);
}

static Napi::Value Close(const Napi::CallbackInfo &info) {
  CloseEmbeddedPreview();
  return info.Env().Undefined();
}

static Napi::Object Init(Napi::Env env, Napi::Object exports) {
  exports.Set("show", Napi::Function::New(env, Show));
  exports.Set("update", Napi::Function::New(env, Update));
  exports.Set("close", Napi::Function::New(env, Close));
  return exports;
}

NODE_API_MODULE(system_preview, Init)
