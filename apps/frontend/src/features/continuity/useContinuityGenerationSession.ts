import type { BBox } from "../../api/client";
import {
  useGenerationSession,
  type AcceptedVariantPayload,
} from "../../hooks/useGenerationSession";
import type { Clip } from "../../stores/edl";

type UseContinuityGenerationSessionArgs = {
  clip: Clip | null;
  bbox: BBox | null;
  previewFrameTs: number | null;
  onAccepted?: (payload: AcceptedVariantPayload) => void | Promise<void>;
};

export function useContinuityGenerationSession({
  clip,
  bbox,
  previewFrameTs,
  onAccepted,
}: UseContinuityGenerationSessionArgs) {
  return useGenerationSession({
    clip,
    bbox,
    previewFrameTs,
    onAccepted,
  });
}
