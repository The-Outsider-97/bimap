"use client";

import { useEffect, type RefObject } from "react";

export function useClickOutside<T extends HTMLElement>(
  ref: RefObject<T | null>,
  onOutside: () => void,
  enabled = true,
) {
  useEffect(() => {
    if (!enabled) return;

    const handlePointer = (event: PointerEvent) => {
      const node = ref.current;
      if (node && !node.contains(event.target as Node)) {
        onOutside();
      }
    };

    document.addEventListener("pointerdown", handlePointer);

    return () => {
      document.removeEventListener("pointerdown", handlePointer);
    };
  }, [enabled, onOutside, ref]);
}
