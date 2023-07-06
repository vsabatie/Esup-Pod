// Récupérer les champs opacity et position
var opacityField = document.getElementById('id_opacity');
var positionField = document.getElementById('id_position');
var watermarkField = document.getElementById('id_watermark');


// Désactiver les champs opacity et position au chargement de la page
if (watermarkField.value !== '') {
    console.log("trouduc");
    opacityField.disabled = false;
    positionField.disabled = false;
} else {
    console.log("troudeballe");
    opacityField.disabled = true;
    positionField.disabled = true;
}

// Créer un observateur de mutation
var observer = new MutationObserver(function(mutations) {
    mutations.forEach(function(mutation) {
        if (mutation.attributeName === 'value') {
            handleWatermarkChange();
        }
    });
});

// Observer les mutations de l'attribut "value" du champ watermark
observer.observe(watermarkField, { attributes: true });

// Gérer l'état des champs lors de la modification du champ watermark
function handleWatermarkChange() {
    if (watermarkField.value !== '') {
        opacityField.disabled = false;
        positionField.disabled = false;
    } else {
        opacityField.disabled = true;
        positionField.disabled = true;
    }
};