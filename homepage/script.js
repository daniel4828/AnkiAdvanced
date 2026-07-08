// 主页脚本——目前只做一件事：页脚年份自动更新。
// 以后需要交互功能（主题切换按钮、动画等）就往这里加。
document.getElementById("year").textContent = new Date().getFullYear();
