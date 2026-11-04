import { TeamPage } from "./features/workspace/Workflow";
import { AnalysisCenterPage } from "./features/workspace/AnalysisCenter";
import { Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { AggregatePage } from "./features/aggregate-detail/AggregatePage";
import { CorrectionsPage } from "./features/corrections/CorrectionsPage";
import { EventPage } from "./features/event/EventPage";
import { AdminPage } from "./features/auth/AdminPage";
import { InvitePage, LoginPage, RequireAuth } from "./features/auth/AuthPages";
import { ObservationsPage } from "./features/observations/ObservationsPage";
import { ImportPage } from "./features/import/ImportPage";
import { DataGuidePage, DataHubPage } from "./features/import/DataHubPage";
import { SummaryPage } from "./features/findings/SummaryPage";
import { RunLayout } from "./features/runs/RunLayout";
import { CoachHome, CoachReportPage, CoachReviewPage, OperationsPage, RosterPage } from "./features/workspace/CoachWorkspace";
import { ScenePage } from "./features/scene/ScenePage";
import { UploadPage } from "./features/upload/UploadPage";
import { VideoPage } from "./features/video/VideoPage";
import { ResearchLibraryPage } from "./features/research/ResearchLibrary";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="login" element={<LoginPage />} />
        <Route path="invite/:token" element={<InvitePage />} />
        <Route element={<RequireAuth />}>
          <Route index element={<CoachHome />} />
          <Route path="athletes" element={<RosterPage />} />
          <Route path="review-queue" element={<CoachHome reviewOnly />} />
          <Route path="team" element={<TeamPage />} />
          <Route path="analysis" element={<AnalysisCenterPage />} />
          <Route path="research" element={<ResearchLibraryPage />} />
          <Route path="operations" element={<OperationsPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="data-guide" element={<DataGuidePage />} />
          <Route path="videos/:videoId/data" element={<DataHubPage />} />
          <Route path="admin" element={<AdminPage />} />
          <Route path="videos/:videoId" element={<VideoPage />} />
          <Route path="videos/:videoId/import" element={<ImportPage />} />
          <Route path="videos/:videoId/observations" element={<ObservationsPage />} />
          <Route path="runs/:runId" element={<RunLayout />}>
            <Route index element={<SummaryPage />} />
            <Route path="summary" element={<SummaryPage />} />
            <Route path="scene" element={<ScenePage />} />
            <Route path="corrections" element={<CorrectionsPage />} />
            <Route path="review" element={<CoachReviewPage />} />
            <Route path="report" element={<CoachReportPage />} />
            <Route path="aggregates/:aggregateId" element={<AggregatePage />} />
            <Route path="events/:eventId" element={<EventPage />} />
          </Route>
        </Route>
        <Route path="*" element={<p className="notice notice--muted">페이지를 찾을 수 없습니다.</p>} />
      </Route>
    </Routes>
  );
}
