#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

/// 生成 macOS 应用图标与 DMG 背景，避免打包结果依赖设计工具或网络资源。

static NSColor *Color(unsigned int hex, CGFloat alpha) {
    return [NSColor colorWithCalibratedRed:((hex >> 16) & 0xff) / 255.0
                                     green:((hex >> 8) & 0xff) / 255.0
                                      blue:(hex & 0xff) / 255.0
                                     alpha:alpha];
}

static void CenteredText(NSString *text, NSRect rect, NSFont *font, NSColor *textColor) {
    NSMutableParagraphStyle *style = [[NSMutableParagraphStyle alloc] init];
    style.alignment = NSTextAlignmentCenter;
    [text drawInRect:rect withAttributes:@{
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: textColor,
        NSParagraphStyleAttributeName: style,
    }];
}

static void SavePNG(NSInteger width, NSInteger height, NSString *path, void (^draw)(void)) {
    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
        initWithBitmapDataPlanes:NULL
                      pixelsWide:width
                      pixelsHigh:height
                   bitsPerSample:8
                 samplesPerPixel:4
                        hasAlpha:YES
                        isPlanar:NO
                  colorSpaceName:NSDeviceRGBColorSpace
                     bytesPerRow:0
                    bitsPerPixel:0];
    [NSGraphicsContext saveGraphicsState];
    [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap]];
    draw();
    [NSGraphicsContext restoreGraphicsState];

    NSData *data = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    if (![data writeToFile:path atomically:YES]) {
        [NSException raise:@"MacAssetGenerator" format:@"无法写入 %@", path];
    }
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *outputDirectory = argc > 1
            ? [NSString stringWithUTF8String:argv[1]]
            : @"frontend/build";
        [[NSFileManager defaultManager] createDirectoryAtPath:outputDirectory
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:nil];

        SavePNG(1024, 1024, [outputDirectory stringByAppendingPathComponent:@"icon-1024.png"], ^{
            [NSColor.clearColor setFill];
            NSRectFill(NSMakeRect(0, 0, 1024, 1024));

            NSRect iconFrame = NSMakeRect(72, 72, 880, 880);
            NSBezierPath *icon = [NSBezierPath bezierPathWithRoundedRect:iconFrame xRadius:205 yRadius:205];
            [Color(0x111614, 1) setFill];
            [icon fill];

            NSBezierPath *border = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(iconFrame, 12, 12)
                                                                   xRadius:194
                                                                   yRadius:194];
            border.lineWidth = 4;
            [Color(0x42df87, 0.10) setStroke];
            [border stroke];

            NSBezierPath *orbit = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(206, 206, 612, 612)];
            orbit.lineWidth = 5;
            [Color(0x42df87, 0.12) setStroke];
            [orbit stroke];

            NSBezierPath *mark = [NSBezierPath bezierPath];
            [mark moveToPoint:NSMakePoint(300, 738)];
            [mark curveToPoint:NSMakePoint(512, 512)
                 controlPoint1:NSMakePoint(347, 644)
                 controlPoint2:NSMakePoint(416, 574)];
            [mark moveToPoint:NSMakePoint(724, 738)];
            [mark curveToPoint:NSMakePoint(512, 512)
                 controlPoint1:NSMakePoint(677, 644)
                 controlPoint2:NSMakePoint(608, 574)];
            [mark moveToPoint:NSMakePoint(512, 512)];
            [mark curveToPoint:NSMakePoint(512, 278)
                 controlPoint1:NSMakePoint(512, 426)
                 controlPoint2:NSMakePoint(512, 356)];
            mark.lineWidth = 88;
            mark.lineCapStyle = NSLineCapStyleRound;
            mark.lineJoinStyle = NSLineJoinStyleRound;
            [Color(0x42df87, 1) setStroke];
            [mark stroke];
        });

        SavePNG(720, 440, [outputDirectory stringByAppendingPathComponent:@"dmg-background.png"], ^{
            [Color(0xf5f7fa, 1) setFill];
            NSRectFill(NSMakeRect(0, 0, 720, 440));
            [Color(0xffffff, 1) setFill];
            NSRectFill(NSMakeRect(0, 316, 720, 124));
            [Color(0x42df87, 1) setFill];
            NSRectFill(NSMakeRect(0, 436, 720, 4));

            CenteredText(@"安装 引力 YINLI",
                         NSMakeRect(80, 365, 560, 34),
                         [NSFont systemFontOfSize:25 weight:NSFontWeightSemibold],
                         Color(0x20232b, 1));
            CenteredText(@"将左侧应用拖到右侧 Applications 文件夹",
                         NSMakeRect(80, 335, 560, 24),
                         [NSFont systemFontOfSize:14 weight:NSFontWeightRegular],
                         Color(0x68707d, 1));
            CenteredText(@"拖到这里安装",
                         NSMakeRect(270, 244, 180, 24),
                         [NSFont systemFontOfSize:14 weight:NSFontWeightMedium],
                         Color(0x238b57, 1));

            NSBezierPath *arrow = [NSBezierPath bezierPath];
            [arrow moveToPoint:NSMakePoint(278, 207)];
            [arrow curveToPoint:NSMakePoint(442, 207)
                  controlPoint1:NSMakePoint(326, 176)
                  controlPoint2:NSMakePoint(394, 176)];
            arrow.lineWidth = 5;
            arrow.lineCapStyle = NSLineCapStyleRound;
            [Color(0x238b57, 1) setStroke];
            [arrow stroke];

            NSBezierPath *arrowHead = [NSBezierPath bezierPath];
            [arrowHead moveToPoint:NSMakePoint(421, 224)];
            [arrowHead lineToPoint:NSMakePoint(444, 207)];
            [arrowHead lineToPoint:NSMakePoint(425, 188)];
            arrowHead.lineWidth = 5;
            arrowHead.lineCapStyle = NSLineCapStyleRound;
            arrowHead.lineJoinStyle = NSLineJoinStyleRound;
            [Color(0x238b57, 1) setStroke];
            [arrowHead stroke];

            CenteredText(@"安装完成后，可从“应用程序”中打开",
                         NSMakeRect(120, 35, 480, 22),
                         [NSFont systemFontOfSize:12 weight:NSFontWeightRegular],
                         Color(0x858c98, 1));
        });

        printf("macOS 视觉资产已输出到 %s\n", outputDirectory.UTF8String);
    }
    return 0;
}
