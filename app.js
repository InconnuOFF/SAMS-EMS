document.addEventListener("DOMContentLoaded",()=>{
  const tabs=[...document.querySelectorAll(".tab")], panels=[...document.querySelectorAll(".tab-panel")];
  const activate=id=>{tabs.forEach(t=>t.classList.toggle("active",t.dataset.tab===id));panels.forEach(p=>p.classList.toggle("active",p.id===id));};
  tabs.forEach(t=>t.addEventListener("click",()=>{activate(t.dataset.tab);history.replaceState(null,"","#"+t.dataset.tab)}));
  if(location.hash && document.getElementById(location.hash.slice(1))) activate(location.hash.slice(1));
  setTimeout(()=>document.querySelectorAll(".flash").forEach(f=>f.style.opacity=".2"),4500);
});
