"use client";

import { useEffect, useState } from "react";

export function useHeaderVisibility(forceVisible = false) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    let lastY = window.scrollY;
    let lastDirection: "up" | "down" = "up";
    let topReveal = false;

    const applyVisibility = () => {
      const currentY = window.scrollY;

      if (forceVisible || currentY < 72 || topReveal) {
        setVisible(true);
        return;
      }

      setVisible(lastDirection === "up");
    };

    const onScroll = () => {
      const currentY = window.scrollY;

      if (currentY > lastY + 6) {
        lastDirection = "down";
      } else if (currentY < lastY - 6) {
        lastDirection = "up";
      }

      lastY = currentY;
      applyVisibility();
    };

    const onPointerMove = (event: PointerEvent) => {
      const nextTopReveal = event.clientY <= 12;

      if (nextTopReveal !== topReveal) {
        topReveal = nextTopReveal;
        applyVisibility();
      }
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("pointermove", onPointerMove, { passive: true });

    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("pointermove", onPointerMove);
    };
  }, [forceVisible]);

  useEffect(() => {
    if (forceVisible) {
      setVisible(true);
    }
  }, [forceVisible]);

  return visible;
}
