#!/usr/bin/env swift

import CoreGraphics
import Foundation

struct Source: Decodable {
    let path: String
    let positions: [Int]
}

struct Plan: Decodable {
    let output: String
    let sources: [Source]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func mm(_ value: Double) -> CGFloat {
    CGFloat(value * 72.0 / 25.4)
}

func cellOrigin(_ position: Int) -> (Double, Double) {
    let gridX = [11.5, 75.5, 139.5]
    let gridY = [18.5, 85.5, 152.5, 219.5]
    let index = position - 1
    return (gridX[index % 3], gridY[index / 3])
}

func pdfRect(x: Double, yFromTop: Double, width: Double, height: Double) -> CGRect {
    CGRect(
        x: mm(x),
        y: mm(297.0 - yFromTop - height),
        width: mm(width),
        height: mm(height)
    )
}

func drawRegistrationMarks(in context: CGContext, page: CGRect) {
    let inset = mm(5.0)
    let length = mm(7.0)
    let left = inset
    let right = page.width - inset
    let bottom = inset
    let top = page.height - inset

    context.saveGState()
    context.setStrokeColor(CGColor(gray: 0, alpha: 1))
    context.setLineWidth(mm(0.35))
    context.setLineCap(.square)

    let segments = [
        (CGPoint(x: left, y: top), CGPoint(x: left + length, y: top)),
        (CGPoint(x: left, y: top), CGPoint(x: left, y: top - length)),
        (CGPoint(x: right, y: top), CGPoint(x: right - length, y: top)),
        (CGPoint(x: right, y: top), CGPoint(x: right, y: top - length)),
        (CGPoint(x: left, y: bottom), CGPoint(x: left + length, y: bottom)),
        (CGPoint(x: left, y: bottom), CGPoint(x: left, y: bottom + length)),
        (CGPoint(x: right, y: bottom), CGPoint(x: right - length, y: bottom)),
        (CGPoint(x: right, y: bottom), CGPoint(x: right, y: bottom + length)),
    ]
    for (start, end) in segments {
        context.move(to: start)
        context.addLine(to: end)
    }
    context.strokePath()
    context.restoreGState()
}

guard CommandLine.arguments.count == 2 else {
    fail("Usage: merge_label_pdfs.swift PLAN.json")
}

let planURL = URL(fileURLWithPath: CommandLine.arguments[1])
let plan: Plan
do {
    plan = try JSONDecoder().decode(Plan.self, from: Data(contentsOf: planURL))
} catch {
    fail("Could not read aggregate plan: \(error)")
}

var pageRect = CGRect(x: 0, y: 0, width: mm(210.0), height: mm(297.0))
let outputURL = URL(fileURLWithPath: plan.output) as CFURL
guard let consumer = CGDataConsumer(url: outputURL) else {
    fail("Could not create aggregate PDF output.")
}
guard let context = CGContext(consumer: consumer, mediaBox: &pageRect, nil) else {
    fail("Could not create aggregate PDF context.")
}

context.beginPDFPage(nil)
context.setFillColor(CGColor(gray: 1, alpha: 1))
context.fill(pageRect)

for source in plan.sources {
    let sourceURL = URL(fileURLWithPath: source.path) as CFURL
    guard let document = CGPDFDocument(sourceURL), let page = document.page(at: 1) else {
        fail("Could not open source PDF: \(source.path)")
    }
    let transform = page.getDrawingTransform(
        .mediaBox,
        rect: pageRect,
        rotate: 0,
        preserveAspectRatio: true
    )

    for position in source.positions {
        guard (1...12).contains(position) else {
            fail("Invalid grid position \(position) in \(source.path)")
        }
        let (cellX, cellY) = cellOrigin(position)
        let regions = [
            pdfRect(x: cellX + 11.5, yFromTop: cellY, width: 36.0, height: 53.0),
            pdfRect(x: cellX, yFromTop: cellY + 55.0, width: 59.0, height: 4.0),
        ]
        for region in regions {
            context.saveGState()
            context.clip(to: region)
            context.concatenate(transform)
            context.drawPDFPage(page)
            context.restoreGState()
        }
    }
}

drawRegistrationMarks(in: context, page: pageRect)
context.endPDFPage()
context.closePDF()
