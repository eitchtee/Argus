import Swal from 'sweetalert2';

// Global defaults so every call site gets daisyUI buttons instead of SweetAlert's
// own (hardcoded purple/red/grey) ones. `buttonsStyling: false` is what drops the
// inline colors SweetAlert would otherwise paint over any class we add.
// Call sites can still pass their own `customClass` to override these.
window.Swal = Swal.mixin({
  buttonsStyling: false,
  customClass: {
    confirmButton: 'btn btn-primary',
    denyButton: 'btn btn-error',
    cancelButton: 'btn btn-soft',
    actions: 'gap-2',
  },
});
