document.addEventListener("DOMContentLoaded", () => {
    const unitSelect = document.getElementById("id_progress_unit");
    const customUnitWrap = document.querySelector(".gd-custom-unit");
    const customUnitInput = document.getElementById(
        "id_custom_progress_unit"
    );
    const weeklyLabel = document.querySelector(
        "[data-weekly-target-label]"
    );
    const weeklyHelp = document.querySelector(
        "[data-weekly-target-help]"
    );
    const progressFields = document.querySelectorAll(
        "[data-progress-field]"
    );

    if (!unitSelect) {
        return;
    }

    const unitCopy = {
        minutes: {
            weekly: "How many minutes do you want to spend each week?",
            help: "Use the total number of goal-focused minutes you want each week.",
            actionLabel: "Progress is measured by the time above.",
            actionHelp: "BeyondScreen will use the action duration automatically.",
            hideProgress: true,
        },
        sessions: {
            weekly: "How many sessions do you want to complete each week?",
            help: "Each completed small, regular or bigger step counts as one session.",
            actionLabel: "This counts as one completed session.",
            actionHelp: "No extra number is needed.",
            hideProgress: true,
        },
        tasks: {
            weekly: "How many tasks do you want to complete each week?",
            help: "Choose the total number of meaningful tasks you want to finish.",
            actionLabel: "How many tasks will this complete?",
            actionHelp: "Enter a whole number.",
            hideProgress: false,
        },
        questions: {
            weekly: "How many questions do you want to solve each week?",
            help: "Choose a realistic number of questions for one week.",
            actionLabel: "How many questions will this complete?",
            actionHelp: "Enter a whole number.",
            hideProgress: false,
        },
        pages: {
            weekly: "How many pages do you want to complete each week?",
            help: "This can mean pages read, written or revised.",
            actionLabel: "How many pages will this complete?",
            actionHelp: "Enter a whole number.",
            hideProgress: false,
        },
        workouts: {
            weekly: "How many workouts do you want to complete each week?",
            help: "Each planned workout should count as one completed workout.",
            actionLabel: "How many workouts will this complete?",
            actionHelp: "Most actions will complete one workout.",
            hideProgress: false,
        },
        custom: {
            weekly: "What is your weekly aim?",
            help: "Use the custom measurement you named.",
            actionLabel: "How much will this step complete?",
            actionHelp: "Enter a whole number using your custom measurement.",
            hideProgress: false,
        },
    };

    function updateGuidance() {
        const selected = unitSelect.value;
        const copy = unitCopy[selected];

        const isCustom = selected === "custom";
        customUnitWrap.hidden = !isCustom;
        customUnitInput.required = isCustom;

        if (!copy) {
            weeklyLabel.textContent = "What is your weekly aim?";
            weeklyHelp.textContent =
                "Choose a realistic amount you would feel proud to complete in one week.";

            progressFields.forEach((field) => {
                field.hidden = false;
            });
            return;
        }

        weeklyLabel.textContent = copy.weekly;
        weeklyHelp.textContent = copy.help;

        progressFields.forEach((field) => {
            const label = field.querySelector(
                "[data-progress-label]"
            );
            const help = field.querySelector(
                "[data-progress-help]"
            );
            const input = field.querySelector("input");

            label.textContent = copy.actionLabel;
            help.textContent = copy.actionHelp;
            field.hidden = copy.hideProgress;
            input.required = !copy.hideProgress;
        });
    }

    unitSelect.addEventListener("change", updateGuidance);
    updateGuidance();
});
