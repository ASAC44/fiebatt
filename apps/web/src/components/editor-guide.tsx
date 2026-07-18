"use client";

import { BookOpen, ExternalLink, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import "./editor-guide.css";

const steps = [
  "Visit the Fiebatt app and create an account.",
  "Log in using the same credentials.",
  "Click New Project and upload a short MP4 video.",
  'Scrub to the frame you want to edit, draw a bounding box around the object or region, and enter your editing request in the chat — for example, “Change the jacket to red.”',
  "Wait for the generation to complete. This may take a few minutes.",
  "Compare the Original and Modified versions in the preview panel.",
  "Click Accept on your preferred result to add it to the timeline.",
  "Click Export, wait for rendering to finish, and download the final video.",
];

const appUrl = "https://frontend-production-bbd7.up.railway.app";

export function EditorGuide({ onClose }: { onClose: () => void }) {
  return (
    <div
      aria-labelledby="editor-guide-title"
      aria-modal="true"
      className="editor-guide__backdrop"
      role="dialog"
      onClick={onClose}
    >
      <section className="editor-guide" onClick={(event) => event.stopPropagation()}>
        <div className="editor-guide__header">
          <div>
            <div className="editor-guide__eyebrow">getting started</div>
            <h2 className="editor-guide__title" id="editor-guide-title">
              Test Fiebatt
            </h2>
            <p className="editor-guide__intro">
              Follow this loop to make your first video edit.
            </p>
          </div>
          <Button aria-label="Close guide" className="editor-guide__close" onClick={onClose} size="icon" variant="ghost">
            <X />
          </Button>
        </div>

        <a className="editor-guide__app-link" href={appUrl} rel="noreferrer" target="_blank">
          <span>Open the Fiebatt app</span>
          <ExternalLink />
        </a>

        <ol className="editor-guide__steps">
          {steps.map((step) => (
            <li key={step}>
              <span className="editor-guide__number" aria-hidden="true" />
              <span>{step}</span>
            </li>
          ))}
        </ol>

        <div className="editor-guide__tips">
          <div className="editor-guide__tips-title">Tips for better results</div>
          <ul>
            <li>Short videos (5–15 seconds) generally process faster.</li>
            <li>One edit request per prompt produces the best results.</li>
            <li>If you are not satisfied, modify the prompt and generate another variant.</li>
          </ul>
        </div>
      </section>
    </div>
  );
}

export function EditorGuideButton({ onClick }: { onClick: () => void }) {
  return (
    <Button aria-label="Open testing guide" className="h-8 shrink-0 px-2 text-xs md:px-2.5" onClick={onClick} variant="ghost">
      <BookOpen />
      Guide
    </Button>
  );
}
