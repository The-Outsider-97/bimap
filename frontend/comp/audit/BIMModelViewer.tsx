"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import {
  normalizeElementAlias,
  type AuditElementTarget,
  type AuditSeverity,
} from "@/lib/audit-api";

import styles from "./AuditWorkspace.module.css";


type Props = {
  modelUrl: string | null;
  targets: readonly AuditElementTarget[];
  selectedTargetKey: string | null;
  selectedSeverity: AuditSeverity | null;
  onElementSelected: (alias: string) => void;
};

type ViewerState = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  root: THREE.Group;
  markerRoot: THREE.Group;
  raycaster: THREE.Raycaster;
  pointer: THREE.Vector2;
  aliases: Map<string, THREE.Object3D>;
  materialState: Map<THREE.Mesh, THREE.Material | THREE.Material[]>;
  highlightedMeshes: Set<THREE.Mesh>;
};


function aliasCandidates(object: THREE.Object3D): string[] {
  const values: string[] = [];
  if (object.name.trim()) {
    values.push(object.name);
  }

  const source = object.userData as Record<string, unknown>;
  const keys = [
    "GlobalId",
    "globalId",
    "global_id",
    "guid",
    "ifc_guid",
    "expressID",
    "expressId",
    "express_id",
    "elementId",
    "element_id",
    "revitId",
    "revit_id",
  ];
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) {
      values.push(value.trim());
    } else if (typeof value === "number" && Number.isFinite(value)) {
      values.push(String(value));
    }
  }
  return values;
}

function indexAliases(root: THREE.Object3D): Map<string, THREE.Object3D> {
  const index = new Map<string, THREE.Object3D>();
  root.traverse((object) => {
    for (const alias of aliasCandidates(object)) {
      const normalized = normalizeElementAlias(alias);
      if (normalized && !index.has(normalized)) {
        index.set(normalized, object);
      }
    }
  });
  return index;
}

function frameBox(
  state: ViewerState,
  box: THREE.Box3,
  padding = 1.45,
): void {
  if (box.isEmpty()) {
    return;
  }

  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDimension = Math.max(size.x, size.y, size.z, 0.1);
  const verticalFov = THREE.MathUtils.degToRad(state.camera.fov);
  const distance = (maxDimension / (2 * Math.tan(verticalFov / 2))) * padding;
  const direction = state.camera.position
    .clone()
    .sub(state.controls.target)
    .normalize();

  if (!Number.isFinite(direction.lengthSq()) || direction.lengthSq() < 0.01) {
    direction.set(1, 0.8, 1);
  }

  state.camera.position.copy(center.clone().add(direction.multiplyScalar(distance)));
  state.camera.near = Math.max(distance / 1000, 0.001);
  state.camera.far = Math.max(distance * 100, 1000);
  state.camera.updateProjectionMatrix();
  state.controls.target.copy(center);
  state.controls.update();
}

function frameObject(state: ViewerState, object: THREE.Object3D): void {
  frameBox(state, new THREE.Box3().setFromObject(object));
}

function materialHighlight(
  material: THREE.Material,
  severity: AuditSeverity | null,
): THREE.Material {
  const clone = material.clone();
  const candidate = clone as THREE.MeshStandardMaterial;

  if (candidate.color instanceof THREE.Color) {
    candidate.color.set(
      severity === "critical"
        ? 0xff3b30
        : severity === "high"
          ? 0xff7a00
          : severity === "medium"
            ? 0xffcc00
            : severity === "low"
              ? 0x4da3ff
              : 0x8e8e93,
    );
  }
  if (candidate.emissive instanceof THREE.Color) {
    candidate.emissive.set(0x2b1010);
    candidate.emissiveIntensity = 0.65;
  }
  candidate.transparent = true;
  candidate.opacity = 0.9;
  candidate.depthWrite = true;
  return clone;
}

function disposeMaterial(material: THREE.Material | THREE.Material[]): void {
  if (Array.isArray(material)) {
    for (const item of material) {
      item.dispose();
    }
    return;
  }
  material.dispose();
}

function clearHighlight(state: ViewerState): void {
  for (const mesh of state.highlightedMeshes) {
    const original = state.materialState.get(mesh);
    if (!original) {
      continue;
    }
    disposeMaterial(mesh.material);
    mesh.material = original;
  }
  state.highlightedMeshes.clear();
  state.materialState.clear();
  for (const child of [...state.markerRoot.children]) {
    state.markerRoot.remove(child);
    disposeObject(child);
  }
}

function highlightObject(
  state: ViewerState,
  object: THREE.Object3D,
  severity: AuditSeverity | null,
): void {
  object.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) {
      return;
    }
    state.materialState.set(child, child.material);
    child.material = Array.isArray(child.material)
      ? child.material.map((item) => materialHighlight(item, severity))
      : materialHighlight(child.material, severity);
    state.highlightedMeshes.add(child);
  });
}

function createPointMarker(
  point: AuditElementTarget["point"],
  severity: AuditSeverity | null,
): THREE.Mesh | null {
  if (!point) {
    return null;
  }
  const geometry = new THREE.SphereGeometry(0.13, 24, 16);
  const material = new THREE.MeshStandardMaterial({
    color:
      severity === "critical"
        ? 0xff3b30
        : severity === "high"
          ? 0xff7a00
          : 0xffcc00,
    emissive: 0x551111,
    emissiveIntensity: 0.7,
  });
  const marker = new THREE.Mesh(geometry, material);
  marker.position.set(point.x, point.y, point.z);
  return marker;
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) {
      return;
    }
    object.geometry?.dispose();
    disposeMaterial(object.material);
  });
}

function resolveTargetObject(
  state: ViewerState,
  target: AuditElementTarget,
): THREE.Object3D | null {
  for (const alias of target.aliases) {
    const object = state.aliases.get(normalizeElementAlias(alias));
    if (object) {
      return object;
    }
  }
  return null;
}

export function BIMModelViewer({
  modelUrl,
  targets,
  selectedTargetKey,
  selectedSeverity,
  onElementSelected,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<ViewerState | null>(null);
  const [loadState, setLoadState] = useState<"empty" | "loading" | "ready" | "error">("empty");
  const [message, setMessage] = useState("Load a GLB/GLTF model to enable spatial navigation.");

  const selectedTarget = useMemo(
    () => targets.find((target) => target.key === selectedTargetKey) ?? null,
    [selectedTargetKey, targets],
  );

  const targetAliases = useMemo(
    () => new Set(
      targets.flatMap((target) =>
        target.aliases.map(normalizeElementAlias),
      ),
    ),
    [targets],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf4f5f7);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100000);
    camera.position.set(8, 6, 8);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 2.2));
    const keyLight = new THREE.DirectionalLight(0xffffff, 3.2);
    keyLight.position.set(12, 18, 10);
    keyLight.castShadow = true;
    scene.add(keyLight);

    const root = new THREE.Group();
    const markerRoot = new THREE.Group();
    scene.add(root, markerRoot);

    const grid = new THREE.GridHelper(40, 40, 0xadb5bd, 0xdde2e6);
    const gridMaterial = grid.material as THREE.LineBasicMaterial;
    gridMaterial.transparent = true;
    gridMaterial.opacity = 0.45;
    scene.add(grid);

    const state: ViewerState = {
      scene,
      camera,
      renderer,
      controls,
      root,
      markerRoot,
      raycaster: new THREE.Raycaster(),
      pointer: new THREE.Vector2(),
      aliases: new Map(),
      materialState: new Map(),
      highlightedMeshes: new Set(),
    };
    stateRef.current = state;

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    let frame = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(render);
    };
    render();

    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      clearHighlight(state);
      disposeObject(root);
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      stateRef.current = null;
    };
  }, []);

  useEffect(() => {
    const state = stateRef.current;
    if (!state) {
      return;
    }

    clearHighlight(state);
    for (const child of [...state.root.children]) {
      state.root.remove(child);
      disposeObject(child);
    }
    state.aliases.clear();

    if (!modelUrl) {
      setLoadState("empty");
      setMessage("Load a GLB/GLTF model to enable spatial navigation.");
      return;
    }

    let active = true;
    setLoadState("loading");
    setMessage("Loading model geometry…");

    const loader = new GLTFLoader();
    loader.load(
      modelUrl,
      (gltf) => {
        if (!active || stateRef.current !== state) {
          disposeObject(gltf.scene);
          return;
        }
        state.root.add(gltf.scene);
        state.aliases = indexAliases(gltf.scene);
        frameObject(state, gltf.scene);
        setLoadState("ready");
        setMessage(
          `${state.aliases.size.toLocaleString()} model identifiers indexed for finding navigation.`,
        );
      },
      undefined,
      (error) => {
        if (!active) {
          return;
        }
        console.error(error);
        setLoadState("error");
        setMessage("The selected model could not be loaded as GLB/GLTF geometry.");
      },
    );

    return () => {
      active = false;
    };
  }, [modelUrl]);

  useEffect(() => {
    const state = stateRef.current;
    if (!state) {
      return;
    }

    clearHighlight(state);
    if (!selectedTarget) {
      return;
    }

    const object = resolveTargetObject(state, selectedTarget);
    if (object) {
      highlightObject(state, object, selectedSeverity);
      frameObject(state, object);
      return;
    }

    const marker = createPointMarker(selectedTarget.point, selectedSeverity);
    if (marker) {
      state.markerRoot.add(marker);
      const box = new THREE.Box3().setFromCenterAndSize(
        marker.position,
        new THREE.Vector3(1, 1, 1),
      );
      frameBox(state, box, 3);
    }
  }, [selectedSeverity, selectedTarget]);

  const onCanvasClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    const state = stateRef.current;
    const canvas = state?.renderer.domElement;
    if (!state || !canvas || loadState !== "ready") {
      return;
    }

    const rect = canvas.getBoundingClientRect();
    state.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    state.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    state.raycaster.setFromCamera(state.pointer, state.camera);

    const intersections = state.raycaster.intersectObject(state.root, true);
    for (const intersection of intersections) {
      let current: THREE.Object3D | null = intersection.object;
      while (current && current !== state.root) {
        for (const alias of aliasCandidates(current)) {
          if (targetAliases.has(normalizeElementAlias(alias))) {
            onElementSelected(alias);
            return;
          }
        }
        current = current.parent;
      }
    }
  };

  const resetView = () => {
    const state = stateRef.current;
    if (!state || state.root.children.length === 0) {
      return;
    }
    frameObject(state, state.root);
  };

  return (
    <div className={styles.viewerShell}>
      <div className={styles.viewerToolbar}>
        <div>
          <strong>Model navigator</strong>
          <span data-state={loadState}>{message}</span>
        </div>
        <button type="button" onClick={resetView} disabled={loadState !== "ready"}>
          Fit model
        </button>
      </div>
      <div
        ref={hostRef}
        className={styles.viewerCanvas}
        onClick={onCanvasClick}
        role="application"
        aria-label="Interactive BIM model viewer"
      />
      {selectedTarget ? (
        <div className={styles.viewerSelection}>
          <span>Focused object</span>
          <strong>{selectedTarget.label}</strong>
          <small>
            {selectedTarget.kind ?? "BIM element"}
            {selectedTarget.aliases[0] ? ` · ${selectedTarget.aliases[0]}` : ""}
          </small>
        </div>
      ) : null}
    </div>
  );
}
