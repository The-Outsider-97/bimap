"use client";

import type {
  ThreeDProduct,
} from "./types";

type Props = {
  product: ThreeDProduct;
};

export function TurntablePreview({
  product,
}: Props) {

  const {
    videoSrc,
    posterSrc,
  } = product.preview;

  if (videoSrc) {
    return (
      <video
        className="model-turntable__video"

        src={videoSrc}

        poster={posterSrc}

        autoPlay
        muted
        loop
        playsInline

        preload="metadata"

        aria-label={
          `${product.title} rotating preview`
        }
      />
    );
  }

  /*
   * Development fallback.
   *
   * Prevents broken images/videos while
   * the actual product media has not yet
   * been configured.
   */
  return (
    <div
      className="model-turntable__fallback"

      aria-label={
        `${product.title} preview not configured`
      }
    >
      <div
        className="model-turntable__grid"

        aria-hidden="true"
      />

      <div
        className="model-turntable__proxy"

        aria-hidden="true"
      >
        <span />
        <span />
        <span />
      </div>

      <p>
        Turntable preview

        <span>
          not configured
        </span>
      </p>
    </div>
  );
}