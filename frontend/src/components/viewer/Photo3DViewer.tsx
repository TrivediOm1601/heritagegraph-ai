import { useEffect, useRef, useState } from 'react';

/**
 * "View in 3D": turns the photograph into a depth relief you can tilt.
 *
 * Depth comes from a small AI depth model (Depth Anything V2) that runs in the
 * browser; the photo never leaves the device for this step. The model is
 * downloaded only when the user opens the viewer. If it cannot be loaded, an
 * approximate relief (brightness + position) is shown instead and labelled as
 * such. The result is an estimated depth effect, not a scan of the monument.
 */
const TRANSFORMERS_URL = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.1';
const MODEL_ID = 'onnx-community/depth-anything-v2-small';
const MAX_SIDE = 480;

type Phase =
  | { s: 'idle' }
  | { s: 'loading'; msg: string }
  | { s: 'ready'; mode: 'ai' | 'approx' }
  | { s: 'error'; msg: string };

function loadCanvas(src: string): Promise<HTMLCanvasElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.max(2, Math.round(img.naturalWidth * k));
      c.height = Math.max(2, Math.round(img.naturalHeight * k));
      c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height);
      resolve(c);
    };
    img.onerror = () => reject(new Error('The image could not be read.'));
    img.src = src;
  });
}

function blur(d: Float32Array, w: number, h: number, passes: number): Float32Array {
  let a = d;
  for (let p = 0; p < passes; p++) {
    const b = new Float32Array(a.length);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let s = 0, n = 0;
        for (let dy = -1; dy <= 1; dy++)
          for (let dx = -1; dx <= 1; dx++) {
            const xx = x + dx, yy = y + dy;
            if (xx >= 0 && yy >= 0 && xx < w && yy < h) { s += a[yy * w + xx]; n++; }
          }
        b[y * w + x] = s / n;
      }
    a = b;
  }
  return a;
}

/** Fallback: nearer toward the bottom of the frame and for brighter pixels. */
function approxDepth(c: HTMLCanvasElement): Float32Array {
  const { width: w, height: h } = c;
  const px = c.getContext('2d')!.getImageData(0, 0, w, h).data;
  const d = new Float32Array(w * h);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const lum = (0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]) / 255;
      d[y * w + x] = 0.6 * (y / h) + 0.4 * lum;
    }
  return d;
}

async function aiDepth(c: HTMLCanvasElement, onProgress: (p: number) => void): Promise<Float32Array> {
  // Loaded from a CDN on demand, so it adds nothing to the app bundle.
  const mod: any = await import(/* @vite-ignore */ TRANSFORMERS_URL);
  const estimator = await mod.pipeline('depth-estimation', MODEL_ID, {
    dtype: 'q8',
    progress_callback: (p: any) => {
      if (p?.status === 'progress' && typeof p.progress === 'number') onProgress(Math.round(p.progress));
    },
  });
  const out = await estimator(c.toDataURL('image/jpeg', 0.9));
  const img = out.depth;
  const { width: w, height: h } = c;
  const d = new Float32Array(w * h);
  let lo = Infinity, hi = -Infinity;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const sx = Math.min(img.width - 1, Math.floor((x / w) * img.width));
      const sy = Math.min(img.height - 1, Math.floor((y / h) * img.height));
      const v = img.data[sy * img.width + sx];
      d[y * w + x] = v;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  const span = hi - lo || 1;
  for (let i = 0; i < d.length; i++) d[i] = (d[i] - lo) / span;
  return d;
}

async function buildScene(
  canvasEl: HTMLCanvasElement,
  host: HTMLElement,
  photo: HTMLCanvasElement,
  depth: Float32Array,
): Promise<() => void> {
  const THREE = await import('three');
  const renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0f2321);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 10);

  const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
  const aspect = photo.width / photo.height;
  const segX = 200, segY = Math.max(20, Math.round(200 / aspect));
  const geo = new THREE.PlaneGeometry(aspect, 1, segX, segY);
  const smooth = blur(depth, photo.width, photo.height, 2);
  const pos = geo.attributes.position as import('three').BufferAttribute;
  for (let i = 0; i < pos.count; i++) {
    const u = pos.getX(i) / aspect + 0.5, v = pos.getY(i) + 0.5;
    const x = clamp(Math.floor(u * photo.width), 0, photo.width - 1);
    const y = clamp(Math.floor((1 - v) * photo.height), 0, photo.height - 1);
    pos.setZ(i, smooth[y * photo.width + x] * 0.35);
  }
  geo.computeVertexNormals();
  const tex = new THREE.CanvasTexture(photo);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, mat);
  const group = new THREE.Group();
  group.add(mesh);
  group.position.z = -0.15;
  scene.add(group);

  let zoom = 1.7, rx = 0, ry = 0, tx = 0, ty = 0, dragging = false, lastX = 0, lastY = 0, idleAt = performance.now() + 1500;
  const down = (e: PointerEvent) => { dragging = true; lastX = e.clientX; lastY = e.clientY; canvasEl.setPointerCapture(e.pointerId); };
  const move = (e: PointerEvent) => {
    if (!dragging) return;
    ty = clamp(ty + (e.clientX - lastX) * 0.008, -0.7, 0.7);
    tx = clamp(tx + (e.clientY - lastY) * 0.008, -0.45, 0.45);
    lastX = e.clientX; lastY = e.clientY; idleAt = performance.now() + 2500;
  };
  const up = () => { dragging = false; };
  const wheel = (e: WheelEvent) => { e.preventDefault(); zoom = clamp(zoom + e.deltaY * 0.001, 1.0, 2.6); idleAt = performance.now() + 2500; };
  const reset = () => { tx = 0; ty = 0; zoom = 1.7; idleAt = performance.now() + 1500; };
  canvasEl.addEventListener('pointerdown', down);
  canvasEl.addEventListener('pointermove', move);
  canvasEl.addEventListener('pointerup', up);
  canvasEl.addEventListener('pointercancel', up);
  canvasEl.addEventListener('wheel', wheel, { passive: false });
  canvasEl.addEventListener('dblclick', reset);

  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  let raf = 0;
  const loop = (now: number) => {
    if (!dragging && now > idleAt) { ty = Math.sin(now / 1800) * 0.4; tx = Math.sin(now / 2600) * 0.12; }
    rx += (tx - rx) * 0.08; ry += (ty - ry) * 0.08;
    group.rotation.set(rx, ry, 0);
    camera.position.set(0, 0, zoom);
    renderer.render(scene, camera);
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    canvasEl.removeEventListener('pointerdown', down);
    canvasEl.removeEventListener('pointermove', move);
    canvasEl.removeEventListener('pointerup', up);
    canvasEl.removeEventListener('pointercancel', up);
    canvasEl.removeEventListener('wheel', wheel);
    canvasEl.removeEventListener('dblclick', reset);
    geo.dispose(); mat.dispose(); tex.dispose(); renderer.dispose();
  };
}

export function Photo3DViewer({ src }: { src: string }) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ s: 'idle' });
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let dispose: (() => void) | null = null;
    (async () => {
      try {
        setPhase({ s: 'loading', msg: 'Reading the photograph…' });
        const photo = await loadCanvas(src);
        let depth: Float32Array;
        let mode: 'ai' | 'approx' = 'ai';
        try {
          setPhase({ s: 'loading', msg: 'Loading the depth model (first time only)…' });
          depth = await aiDepth(photo, (p) => !cancelled && setPhase({ s: 'loading', msg: `Downloading depth model… ${p}%` }));
        } catch {
          mode = 'approx';
          depth = approxDepth(photo);
        }
        if (cancelled || !canvasRef.current || !hostRef.current) return;
        dispose = await buildScene(canvasRef.current, hostRef.current, photo, depth);
        if (cancelled) { dispose(); dispose = null; return; }
        setPhase({ s: 'ready', mode });
      } catch (err) {
        if (!cancelled) setPhase({ s: 'error', msg: err instanceof Error && /WebGL/i.test(err.message) ? '3D view needs WebGL, which this browser does not provide.' : 'The 3D view could not be created for this image.' });
      }
    })();
    return () => { cancelled = true; dispose?.(); };
  }, [open, src]);

  if (!open) {
    return (
      <button type="button" className="hg-chip" onClick={() => setOpen(true)}>
        View in 3D
      </button>
    );
  }
  return (
    <section aria-label="3D depth view" className="hg-3d">
      <div className="hg-3d-bar">
        <span className="hg-mono">3D depth view</span>
        <button type="button" className="hg-chip hg-chip-light" onClick={() => { setOpen(false); setPhase({ s: 'idle' }); }}>
          Close
        </button>
      </div>
      <div ref={hostRef} className="hg-3d-stage">
        <canvas ref={canvasRef} className="hg-3d-canvas" aria-label="Interactive 3D relief of the photograph" />
        {phase.s === 'loading' ? <p role="status" className="hg-3d-msg">{phase.msg}</p> : null}
        {phase.s === 'error' ? <p role="alert" className="hg-3d-msg">{phase.msg}</p> : null}
      </div>
      <p className="hg-3d-note">
        {phase.s === 'ready' && phase.mode === 'approx'
          ? 'Approximate relief: the depth model could not be loaded, so depth is guessed from brightness and position.'
          : 'Depth is estimated by AI from a single photo. It is an effect, not a scan of the monument.'}{' '}
        Drag to tilt, scroll to zoom, double-click to reset.
      </p>
    </section>
  );
}

export default Photo3DViewer;
