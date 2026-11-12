import Foundation
import Vision
import CoreGraphics
import ImageIO

struct DetectedFace: Codable {
    let box: [Double]      // [x, y, width, height] normalized
    let confidence: Float
    let embedding_b64: String
}

struct ImageFacesResult: Codable {
    let media_id: String
    let path: String
    let faces: [DetectedFace]
}

func processImage(mediaId: String, path: String) -> ImageFacesResult {
    let url = URL(fileURLWithPath: path)
    guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
        return ImageFacesResult(media_id: mediaId, path: path, faces: [])
    }
    
    let faceReq = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    
    do {
        try handler.perform([faceReq])
        guard let faceResults = faceReq.results, !faceResults.isEmpty else {
            return ImageFacesResult(media_id: mediaId, path: path, faces: [])
        }
        
        let imgW = Double(cgImage.width)
        let imgH = Double(cgImage.height)
        var detected: [DetectedFace] = []
        
        for face in faceResults {
            // Filter low confidence faces
            if face.confidence < 0.40 {
                continue
            }
            
            let box = face.boundingBox
            // Crop face with 20% margin
            let padW = box.size.width * 0.20
            let padH = box.size.height * 0.20
            
            let normX = max(0.0, box.origin.x - padW)
            let normY = max(0.0, box.origin.y - padH)
            let normW = min(1.0 - normX, box.size.width + padW * 2)
            let normH = min(1.0 - normY, box.size.height + padH * 2)
            
            // Convert Vision coordinate (bottom-left 0,0) to CGImage coordinate (top-left 0,0)
            let cropPixelX = normX * imgW
            let cropPixelY = (1.0 - normY - normH) * imgH
            let cropPixelW = normW * imgW
            let cropPixelH = normH * imgH
            
            let cropRect = CGRect(x: cropPixelX, y: cropPixelY, width: cropPixelW, height: cropPixelH)
            guard let faceCrop = cgImage.cropping(to: cropRect) else {
                continue
            }
            
            let featReq = VNGenerateImageFeaturePrintRequest()
            let featHandler = VNImageRequestHandler(cgImage: faceCrop, options: [:])
            try featHandler.perform([featReq])
            
            guard let featObs = featReq.results?.first as? VNFeaturePrintObservation else {
                continue
            }
            
            let b64 = featObs.data.base64EncodedString()
            detected.append(DetectedFace(
                box: [round(box.origin.x*10000)/10000,
                      round(box.origin.y*10000)/10000,
                      round(box.size.width*10000)/10000,
                      round(box.size.height*10000)/10000],
                confidence: face.confidence,
                embedding_b64: b64
            ))
        }
        
        return ImageFacesResult(media_id: mediaId, path: path, faces: detected)
    } catch {
        return ImageFacesResult(media_id: mediaId, path: path, faces: [])
    }
}

// Batch mode: reads "media_id\tpath" from stdin, outputs JSON line per image
let encoder = JSONEncoder()
while let line = readLine() {
    let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { continue }
    let parts = trimmed.split(separator: "\t", maxSplits: 1)
    if parts.count == 2 {
        let mid = String(parts[0])
        let p = String(parts[1])
        let result = processImage(mediaId: mid, path: p)
        if let data = try? encoder.encode(result), let str = String(data: data, encoding: .utf8) {
            print(str)
            fflush(stdout)
        }
    }
}
