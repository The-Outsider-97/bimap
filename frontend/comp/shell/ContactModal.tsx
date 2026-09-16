"use client";

import {
  type FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  sendContactMessage,
} from "@/lib/bimap-api";


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

  const [submitting, setSubmitting] =
    useState(false);

  const [ticketNumber, setTicketNumber] =
    useState<string | null>(null);

  const [submitError, setSubmitError] =
    useState<string | null>(null);


  useEffect(() => {
    if (!open) {
      setSubmitting(false);
      setTicketNumber(null);
      setSubmitError(null);

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


  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();

    if (submitting) {
      return;
    }

    const form =
      event.currentTarget;

    const formData =
      new FormData(form);

    const name =
      String(
        formData.get("name") ?? "",
      ).trim();

    const email =
      String(
        formData.get("email") ?? "",
      ).trim();

    const subject =
      String(
        formData.get("subject") ?? "",
      ).trim();

    const message =
      String(
        formData.get("message") ?? "",
      ).trim();


    if (
      !name ||
      !email ||
      !subject ||
      !message
    ) {
      setSubmitError(
        "Please complete all required fields.",
      );

      return;
    }


    setSubmitting(true);
    setTicketNumber(null);
    setSubmitError(null);


    try {
      const result =
        await sendContactMessage({
          name,
          email,
          subject,
          message,
        });

      setTicketNumber(
        result.ticket_number,
      );

      form.reset();
    } catch {
      setSubmitError(
        "Your message could not be sent. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }


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
            <span aria-hidden="true">
              ×
            </span>
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
          onSubmit={handleSubmit}
        >
          <div className="contact-form__row">
            <label>
              <span>
                Name
              </span>

              <input
                name="name"
                type="text"
                autoComplete="name"
                maxLength={128}
                disabled={submitting}
                required
              />
            </label>


            <label>
              <span>
                Email
              </span>

              <input
                name="email"
                type="email"
                autoComplete="email"
                maxLength={254}
                disabled={submitting}
                required
              />
            </label>
          </div>


          <label>
            <span>
              Subject
            </span>

            <select
              name="subject"
              defaultValue="bim-audit"
              disabled={submitting}
              required
            >
              <option value="bim-audit">
                BIM Audit
              </option>

              <option value="revit-audit">
                Revit Audit
              </option>

              <option value="3d-content">
                3D Models &amp; Scenes
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
            <span>
              Message
            </span>

            <textarea
              name="message"
              rows={6}
              maxLength={10_000}
              disabled={submitting}
              required
            />
          </label>


          <div className="contact-form__foot">
            <p
              className="contact-form__status"
              aria-live="polite"
              role="status"
            >
              {submitError
                ? submitError
                : ticketNumber
                  ? `Message sent successfully. Your ticket number is ${ticketNumber}.`
                  : submitting
                    ? "Sending your message..."
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
                disabled={submitting}
                aria-disabled={submitting}
              >
                {submitting
                  ? "Sending..."
                  : "Send message"}

                {!submitting && (
                  <span aria-hidden="true">
                    ↗
                  </span>
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
