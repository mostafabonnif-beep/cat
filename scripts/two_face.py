import cv2

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    mp = None
    MEDIAPIPE_AVAILABLE = False
import numpy as np


def crop_and_maintain_ar(frame, face_box, target_w, target_h, zoom_out_factor=2.2):
    """
    Recorta uma região baseada no rosto mantendo o aspect ratio do target.
    Previne deformação (esticar/espremer).
    """
    img_h, img_w, _ = frame.shape
    x, y, w, h = face_box
    
    # Centro do rosto
    cx = x + w // 2
    cy = y + h // 2
    
    # Dimensão base do rosto (maior lado para garantir cobertura)
    face_size = max(w, h)
    
    # Altura desejada do crop (altura do rosto * fator de zoom/afastamento)
    # zoom_out_factor: quanto maior, mais afastado (mais cenário)
    req_h = face_size * zoom_out_factor
    
    # Aspect Ratio alvo (1080 / 960 = 1.125)
    target_ar = target_w / target_h
    
    # Calcular largura e altura do crop mantendo AR
    crop_h = req_h
    crop_w = crop_h * target_ar
    
    # Verificar limitações da imagem original (não podemos cortar mais que existe)
    # Se a largura necessária for maior que a imagem, limitamos pela largura
    if crop_w > img_w:
        crop_w = float(img_w)
        crop_h = crop_w / target_ar
        
    # Se a altura necessária for maior que a imagem, limitamos pela altura
    if crop_h > img_h:
        crop_h = float(img_h)
        crop_w = crop_h * target_ar
        
    # Converter para inteiros
    crop_w = int(crop_w)
    crop_h = int(crop_h)
    
    # Calcular coordenadas top-left do crop centralizado no rosto
    x1 = int(cx - crop_w // 2)
    y1 = int(cy - crop_h // 2)
    
    # Ajuste de bordas (Clamp) deslisando a janela se possível
    # Se sair pela esquerda, encosta na esquerda
    if x1 < 0: 
        x1 = 0
    # Se sair pela direita, encosta na direita
    elif x1 + crop_w > img_w: 
        x1 = img_w - crop_w
        
    # Se sair por cima
    if y1 < 0: 
        y1 = 0
    # Se sair por baixo
    elif y1 + crop_h > img_h: 
        y1 = img_h - crop_h
    
    # Verificação de segurança final se a imagem for menor que o crop (embora lógica acima evite)
    x2 = x1 + crop_w
    y2 = y1 + crop_h
    
    # Crop
    cropped = frame[y1:y2, x1:x2]
    
    # Se o crop falhar (tamanho 0), retorna preto
    if cropped.size == 0 or cropped.shape[0] == 0 or cropped.shape[1] == 0:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)

    # Redimensionar para o tamanho alvo final (1080x960)
    # Como garantimos o AR, o resize mantém a proporção correta
    resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return resized

def crop_and_resize_two_faces(frame, face_positions, zoom_out_factor=2.2):
    """Backward-compatible two-face vertical composition."""
    return crop_and_resize_multi_faces(
        frame, face_positions, layout="vertical", max_faces=2,
        zoom_out_factor=zoom_out_factor,
    )


def _safe_face_boxes(face_positions, frame_shape, max_faces=4):
    """Normalize face boxes to valid ``(x, y, w, h)`` rectangles."""
    if not face_positions:
        return []
    frame_h, frame_w = frame_shape[:2]
    normalized = []
    for box in face_positions:
        if box is None or len(box) < 4:
            continue
        x, y, w, h = [int(round(float(v))) for v in box[:4]]
        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = max(1, min(w, frame_w - x))
        h = max(1, min(h, frame_h - y))
        normalized.append((x, y, w, h))
    # Stable left-to-right order avoids faces jumping between grid cells.
    normalized.sort(key=lambda b: (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0))
    return normalized[:max(1, int(max_faces))]


def crop_and_resize_multi_faces(frame, face_positions, target_w=1080,
                                target_h=1920, layout="auto", max_faces=4,
                                zoom_out_factor=2.2):
    """Compose one to four tracked faces into a portrait output.

    ``auto`` keeps the legacy two-person vertical stack and uses a 2x2 grid
    for three or four people. ``speaker`` gives the largest face the full
    portrait and places up to three other faces as small thumbnails.
    """
    boxes = _safe_face_boxes(face_positions, frame.shape, max_faces=max_faces)
    if not boxes:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)
    if len(boxes) == 1:
        return crop_and_maintain_ar(frame, boxes[0], target_w, target_h, zoom_out_factor)

    mode = str(layout or "auto").lower()
    if mode == "auto":
        mode = "vertical" if len(boxes) == 2 else "grid"

    if mode == "speaker":
        main = max(boxes, key=lambda b: b[2] * b[3])
        others = [b for b in boxes if b != main][:3]
        result = crop_and_maintain_ar(frame, main, target_w, target_h, zoom_out_factor)
        thumb_w = max(180, target_w // 3)
        thumb_h = max(220, int(thumb_w / (target_w / target_h)))
        margin = max(12, target_w // 60)
        for idx, box in enumerate(others):
            thumb = crop_and_maintain_ar(frame, box, thumb_w, thumb_h, zoom_out_factor)
            x1 = target_w - thumb_w - margin
            y1 = margin + idx * (thumb_h + margin)
            if y1 + thumb_h <= target_h:
                result[y1:y1 + thumb_h, x1:x1 + thumb_w] = thumb
        return result

    if mode in {"vertical", "stack"}:
        rows, cols = len(boxes), 1
    elif mode in {"horizontal", "row"}:
        rows, cols = 1, len(boxes)
    else:
        cols = 2 if len(boxes) > 1 else 1
        rows = int(np.ceil(len(boxes) / float(cols)))

    cell_w = target_w // cols
    cell_h = target_h // rows
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    for idx, box in enumerate(boxes):
        row, col = divmod(idx, cols)
        x1, y1 = col * cell_w, row * cell_h
        w = cell_w if col < cols - 1 else target_w - x1
        h = cell_h if row < rows - 1 else target_h - y1
        crop = crop_and_maintain_ar(frame, box, w, h, zoom_out_factor)
        canvas[y1:y1 + h, x1:x1 + w] = crop
    return canvas


def detect_face_or_body_two_faces(frame, face_detection, face_mesh, pose, max_faces=2):
    # Converter a imagem para RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Processar a detecção de rosto
    results_face_detection = face_detection.process(frame_rgb)
    results_face_mesh = face_mesh.process(frame_rgb)
    results_pose = pose.process(frame_rgb)

    face_positions_detection = []
    if results_face_detection.detections:
        for detection in results_face_detection.detections[:max(1, int(max_faces))]:
            bbox = detection.location_data.relative_bounding_box
            x_min = int(bbox.xmin * frame.shape[1])
            y_min = int(bbox.ymin * frame.shape[0])
            width = int(bbox.width * frame.shape[1])
            height = int(bbox.height * frame.shape[0])
            face_positions_detection.append((x_min, y_min, width, height))

    if len(face_positions_detection) >= max_faces:
        return face_positions_detection

    face_positions_mesh = []
    if results_face_mesh.multi_face_landmarks:
        for landmarks in results_face_mesh.multi_face_landmarks[:max(1, int(max_faces))]:
            x_coords = [int(landmark.x * frame.shape[1]) for landmark in landmarks.landmark]
            y_coords = [int(landmark.y * frame.shape[0]) for landmark in landmarks.landmark]
            x_min, x_max = min(x_coords), max(x_coords)
            y_min, y_max = min(y_coords), max(y_coords)
            width = x_max - x_min
            height = y_max - y_min
            face_positions_mesh.append((x_min, y_min, width, height))

    if len(face_positions_mesh) >= max_faces:
        return face_positions_mesh
        
    # If neither found 2, return what we found (prefer detection as it is bounding box optimized)
    if face_positions_detection:
        return face_positions_detection
    if face_positions_mesh:
        return face_positions_mesh

    # Se nenhum rosto for detectado, usar a pose para estimar o corpo
    if results_pose.pose_landmarks:
        x_coords = [lmk.x for lmk in results_pose.pose_landmarks.landmark]
        y_coords = [lmk.y for lmk in results_pose.pose_landmarks.landmark]
        x_min = int(min(x_coords) * frame.shape[1])
        x_max = int(max(x_coords) * frame.shape[1])
        y_min = int(min(y_coords) * frame.shape[0])
        y_max = int(max(y_coords) * frame.shape[0])
        width = x_max - x_min
        height = y_max - y_min
        return [(x_min, y_min, width, height)]

    return None
