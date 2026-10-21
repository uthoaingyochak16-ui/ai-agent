import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";
import SSO from "./pages/SSO";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const InboxPage = lazy(() => import("./pages/Inbox"));
const Profiles = lazy(() => import("./pages/Profiles"));
const ProfileDetail = lazy(() => import("./pages/ProfileDetail"));
const KnowledgeHub = lazy(() => import("./pages/KnowledgeHub"));
const Channels = lazy(() => import("./pages/Channels"));
const AISettings = lazy(() => import("./pages/AISettings"));
const Train = lazy(() => import("./pages/Train"));
const Leads = lazy(() => import("./pages/Leads"));
const Comments = lazy(() => import("./pages/Comments"));
const Posts = lazy(() => import("./pages/Posts"));

function Protected({ admin }: { admin?: boolean }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking session" />;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/" replace />;
  return <Layout />;
}

export default function App() {
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/sso" element={<SSO />} />
        <Route element={<Protected />}>
          <Route index element={<Dashboard />} />
          <Route path="inbox" element={<InboxPage />} />
          <Route path="profiles" element={<Profiles />} />
          <Route path="profiles/:id" element={<ProfileDetail />} />
          <Route path="leads" element={<Leads />} />
          <Route path="comments" element={<Comments />} />
          <Route path="posts" element={<Posts />} />
          <Route path="knowledge" element={<KnowledgeHub />} />
          <Route path="qa" element={<Navigate to="/knowledge?section=qa" replace />} />
          <Route path="train" element={<Train />} />
          <Route path="evaluation" element={<Navigate to="/train?tab=tests" replace />} />
          <Route path="test-chat" element={<Navigate to="/train?tab=chat" replace />} />
        </Route>
        <Route element={<Protected admin />}>
          <Route path="channels" element={<Channels />} />
          <Route path="ai" element={<AISettings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
