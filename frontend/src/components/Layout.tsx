import { WorkspaceSwitcher, useWorkspaceRole } from "../features/workspace/Workflow";
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { useLogout, useMe } from "../api/hooks";
import { T } from "../strings/ko";

function NavIcon({ kind }: { kind: "library" | "athletes" | "review" | "settings" | "data" }) {
  return <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind === "library" ? <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m10 9 5 3-5 3Z" /></> : kind === "athletes" ? <><circle cx="9" cy="8" r="3" /><path d="M3 20v-2a6 6 0 0 1 12 0v2m1-15a3 3 0 0 1 0 6m3 8v-1a6 6 0 0 0-3-5" /></> : kind === "review" ? <><path d="M9 4H5a2 2 0 0 0-2 2v14h18V6a2 2 0 0 0-2-2h-4M9 3h6v4H9z" /><path d="m8 13 3 3 5-6" /></> : kind === "data" ? <><path d="m8 5-5 7 5 7m8-14 5 7-5 7m-5 0 2-14" /></> : <><circle cx="12" cy="12" r="3" /><path d="m10 3-1 3-3 1-3 3v4l3 3 3 1 1 3h4l1-3 3-1 3-3v-4l-3-3-3-1-1-3Z" /></>}</svg>;
}

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const user = me.data;
  const publicDemo = user?.auth_mode === "dev" && user.role === "participant";
  const readOnly = useWorkspaceRole() === "viewer";
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => { if (mainRef.current) mainRef.current.scrollTop = 0; }, [location.pathname]);

  return <div className={`workspace-shell ${!user ? "workspace-shell--guest" : ""}`}>
    <a className="skip-link" href="#main">{T.skipToContent}</a>
    {user ? <aside className="workspace-sidebar"><Link to="/" className="workspace-brand"><span className="brand-mark" aria-hidden="true">C<span /></span><div>COACHDESK<small>PERFORMANCE WORKSPACE</small></div></Link>{publicDemo ? <p className="notice">공개 체험 공간</p> : <WorkspaceSwitcher />}<div className="sidebar-scroll"><p className="sidebar-caption">WORKSPACE</p><nav aria-label="주 메뉴" className="workspace-navigation"><NavLink to="/" end><NavIcon kind="library" />경기·훈련 기록</NavLink><NavLink to="/athletes"><NavIcon kind="athletes" />선수 명부</NavLink><NavLink to="/review-queue"><NavIcon kind="review" />검수 대기함</NavLink></nav><nav aria-label="분석 안내" className="workspace-navigation"><NavLink to="/research"><NavIcon kind="library" />연구 영상·분석 자료</NavLink><NavLink to="/analysis"><NavIcon kind="data" />영상 분석실</NavLink></nav>{!publicDemo ? <details><summary className="sidebar-caption">고급 관리</summary><nav aria-label="분석실 관리" className="workspace-navigation"><NavLink to="/team"><NavIcon kind="athletes" />팀과 작업 공간</NavLink><NavLink to="/operations"><NavIcon kind="settings" />운영 현황</NavLink><NavLink to="/data-guide"><NavIcon kind="data" />데이터 형식 안내</NavLink>{user.role === "admin" && user.auth_mode === "session" ? <NavLink to="/admin"><NavIcon kind="athletes" />계정 관리</NavLink> : null}</nav></details> : null}</div><div className="sidebar-user"><span className="user-avatar">{user.display_name?.slice(0, 1) ?? "C"}</span><div><strong>{publicDemo ? "공개 체험" : user.auth_mode === "dev" ? "로컬 코치" : user.display_name}</strong><small>{user.auth_mode === "dev" ? "단일 사용자 접속" : "초대 계정"}</small></div>{user.auth_mode === "session" ? <button title="로그아웃" aria-label="로그아웃" disabled={logout.isPending} onClick={() => logout.mutate(undefined, { onSettled: () => void navigate("/login") })}>↗</button> : null}</div></aside> : null}
    <div className="workspace-content"><header className="workspace-topbar"><span>코치 분석실 <span className="topbar-label"> / 영상과 코칭 기록</span></span><span className="local-status"><span className="live-dot" />{user?.auth_mode === "session" ? "계정 접속" : "로컬 작업 공간"}</span></header><main ref={mainRef} id="main" className="workspace-main" tabIndex={-1}>{readOnly ? <p className="notice">현재 공간은 열람 전용입니다. 기록 변경은 팀 소유자나 편집 담당자가 할 수 있습니다.</p> : null}{publicDemo ? <p className="notice">로그인 없는 공개 체험입니다. 이곳에 올린 영상과 기록은 다른 방문자도 볼 수 있습니다.</p> : null}<Outlet /></main></div>
  </div>;
}
