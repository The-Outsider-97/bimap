"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function ContactModal({
  open,
  onClose,
}: Props) {
  const closeButtonRef =
    useRef<HTMLButtonElement>(null);

  const [submitted, setSubmitted] =
    useState(false);

  useEffect(() => {
    if (!open) {
      setSubmitted(false);
      return;
    }

    const previousFocus =
      document.activeElement as HTMLElement | null;

    window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });

    return () => {
      previousFocus?.focus();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="contact-backdrop"
      onMouseDown={onClose}
    >
      <div
        className="contact-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="contact-title"
        aria-describedby="contact-description"
        onMouseDown={(event) =>
          event.stopPropagation()
        }
      >
        <div className="contact-modal__top">
          <div>
            <p className="contact-modal__eyebrow">
              Contact Remy3Design
            </p>

            <h2 id="contact-title">
              How can BIMAP help?
            </h2>
          </div>

          <button
            ref={closeButtonRef}
            className="contact-modal__close"
            type="button"
            onClick={onClose}
            aria-label="Close contact panel"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <p
          id="contact-description"
          className="contact-modal__intro"
        >
          Use this panel for audit questions,
          digital-content enquiries, model
          conversion, data extraction or general
          BIMAP information.
        </p>

        <form
          className="contact-form"
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted(true);
          }}
        >
          <div className="contact-form__row">
            <label>
              <span>Name</span>

              <input
                name="name"
                type="text"
                autoComplete="name"
                required
              />
            </label>

            <label>
              <span>Email</span>

              <input
                name="email"
                type="email"
                autoComplete="email"
                required
              />
            </label>
          </div>

          <label>
            <span>Subject</span>

            <select
              name="subject"
              defaultValue="bim-audit"
            >
              <option value="bim-audit">
                BIM Audit
              </option>

              <option value="revit-audit">
                Revit Audit
              </option>

              <option value="3d-content">
                3D Models & Scenes
              </option>

              <option value="2d-content">
                2D DWG Content
              </option>

              <option value="conversion">
                Model Conversion
              </option>

              <option value="extraction">
                Data Extraction
              </option>

              <option value="other">
                Other
              </option>
            </select>
          </label>

          <label>
            <span>Message</span>

            <textarea
              name="message"
              rows={6}
              required
            />
          </label>

          <div className="contact-form__foot">
            <p
              className="contact-form__status"
              aria-live="polite"
            >
              {submitted
                ? "The interface is ready. Message delivery will be connected to the BIMAP backend contact endpoint."
                : "Do not attach project files through the general contact form."}
            </p>

            <div className="contact-form__actions">
              <button
                type="button"
                className="
                  contact-button
                  contact-button--secondary
                "
                onClick={onClose}
              >
                Cancel
              </button>

              <button
                type="submit"
                className="
                  contact-button
                  contact-button--primary
                "
              >
                Send message

                <span aria-hidden="true">
                  ↗
                </span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}