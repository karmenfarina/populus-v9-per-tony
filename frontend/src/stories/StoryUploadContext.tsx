import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Global "am I currently uploading a story?" gate.
 *
 * The Stories strip on the Home tab needs to know when the user has
 * hit "Pubblica" in the composer but the `POST /api/stories` request
 * is still in flight — that's the window during which the "my" ring
 * must show its rotating gradient (Instagram-style loading state).
 *
 * We share this state via a tiny React context rather than yet
 * another Zustand store — the state is a single boolean and pretty
 * short-lived, so plain React does the job with zero extra
 * dependencies.
 */
type StoryUploadContextValue = {
  isUploading: boolean;
  beginUpload: () => void;
  endUpload: () => void;
};

const StoryUploadContext = createContext<StoryUploadContextValue>({
  isUploading: false,
  beginUpload: () => { /* no-op */ },
  endUpload: () => { /* no-op */ },
});

export function StoryUploadProvider({ children }: { children: React.ReactNode }) {
  // A counter — not just a boolean — because in principle a user
  // could tap "Pubblica" on two composers back-to-back before the
  // first request finishes. The ring stays animated as long as ANY
  // upload is pending.
  const [uploads, setUploads] = useState(0);

  const beginUpload = useCallback(() => {
    setUploads((n) => n + 1);
  }, []);
  const endUpload = useCallback(() => {
    setUploads((n) => Math.max(0, n - 1));
  }, []);

  const value = useMemo<StoryUploadContextValue>(
    () => ({ isUploading: uploads > 0, beginUpload, endUpload }),
    [uploads, beginUpload, endUpload],
  );

  return (
    <StoryUploadContext.Provider value={value}>
      {children}
    </StoryUploadContext.Provider>
  );
}

export function useStoryUpload(): StoryUploadContextValue {
  return useContext(StoryUploadContext);
}
