import { AddressDescription } from "../components/address-description";
import { useManifestMutation } from "@karrio/hooks/manifests";
import { InputField } from "../components/input-field";
import { Manifest, ManifestData } from "@karrio/types/rest/api";
import { useNotifier } from "../components/notifier";
import { AddressForm } from "../forms/address-form";
import { ModalFormProps, useModal } from "./modal";
import { NotificationType } from "@karrio/types";
import { useLoader } from "../components/loader";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@karrio/ui/components/ui/collapsible";
import { useDocumentPrinter } from "@karrio/hooks/resource-token";
import { useRouter } from "next/navigation";
import { isEqual } from "@karrio/lib";
import React from "react";

type CreateManifestModalProps = {
  header?: string;
  manifest: ManifestData;
  connectionOptions?: { carrier_id: string; label: string }[];
};

function reducer(
  state: any,
  {
    name,
    value,
  }: { name: string; value: string | boolean | object | string[] },
): ManifestData {
  switch (name) {
    case "full":
      return { ...(value as ManifestData) };
    case "partial":
      return { ...state, ...(value as ManifestData) };
    default:
      return { ...state, [name]: value };
  }
}

export const CreateManifestModal = ({
  trigger,
  ...args
}: ModalFormProps<CreateManifestModalProps>): JSX.Element => {
  const modal = useModal();

  const ManifestFormComponent = (
    props: CreateManifestModalProps,
  ): JSX.Element => {
    const { manifest: defaultValue, header, connectionOptions } = props;
    const loader = useLoader();
    const { close } = useModal();
    const notifier = useNotifier();
    const mutation = useManifestMutation();
    const documentPrinter = useDocumentPrinter();
    const router = useRouter();
    const [created, setCreated] = React.useState<Manifest | null>(null);
    const [key, setKey] = React.useState<string>(`manifest-${Date.now()}`);
    const [manifest, dispatch] = React.useReducer(
      reducer,
      defaultValue,
      () => defaultValue,
    );

    const handleChange = (event: React.ChangeEvent<any>) => {
      const target = event.target;
      const name: string = target.name;
      let value = target.type === "checkbox" ? target.checked : target.value;

      if (target.multiple === true) {
        value = Array.from(target.selectedOptions).map((o: any) => o.value);
      }

      dispatch({ name, value });
    };
    const handleSubmit = async (e: React.MouseEvent) => {
      e.preventDefault();
      const { carrier_id, ...rest } = manifest;
      // Omit carrier_id entirely when none/empty is selected; the server
      // resolves the connection by carrier_name when it is absent. Never
      // send an empty/false value.
      const payload: ManifestData = carrier_id
        ? { ...rest, carrier_id }
        : rest;
      try {
        loader.setLoading(true);
        const result = await mutation.createManifest.mutateAsync(payload);
        setCreated(result);
        notifier.notify({
          type: NotificationType.success,
          message: "Manifest created successfully!",
        });
        setTimeout(() => {
          close();
          router.push("/manifests");
        }, 4000);
      } catch (message: any) {
        notifier.notify({ type: NotificationType.error, message });
      }
      loader.setLoading(false);
    };

    return (
      <div className="modal-card-body modal-form" key={key}>
        <div className="form-floating-header p-4">
          <span className="has-text-weight-bold is-size-6">
            {header || `Create manifest`}
          </span>
        </div>
        <div className="p-3 my-4"></div>

        {manifest !== undefined && (
          <>
            {/* Shipment IDs section */}
            <div className="field mb-2">
              <div className="control">
                <div className="select is-multiple is-small is-fullwidth">
                  <select
                    disabled
                    name="permissions"
                    value={manifest.shipment_ids}
                    size={3}
                    multiple
                  >
                    {(manifest.shipment_ids || []).map((shipment_id) => (
                      <option
                        key={`${shipment_id}permission-${Date.now()}`}
                        value={shipment_id}
                      >
                        {shipment_id}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* Carrier connection selector (multi-account orgs only) */}
            {(connectionOptions || []).length > 1 && (
              <div className="field mb-2">
                <label className="label is-size-7">Carrier connection</label>
                <div className="control">
                  <div className="select is-small is-fullwidth">
                    <select
                      name="carrier_id"
                      value={manifest.carrier_id || ""}
                      onChange={handleChange}
                    >
                      {(connectionOptions || []).map((opt) => (
                        <option key={opt.carrier_id} value={opt.carrier_id}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            )}

            {/* Address section */}
            <Collapsible className="card px-0 my-3">
              <CollapsibleTrigger
                asChild
              >
                <div className="p-3 is-clickable">
                  <header className="is-flex is-justify-content-space-between">
                    <span className="is-title is-size-7 has-text-weight-bold is-vcentered my-2">
                      ADDRESS
                    </span>
                  </header>

                  <AddressDescription address={manifest.address as any} />
                </div>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <hr className="my-1" style={{ height: "1px" }} />

                <div className="p-3">
                  <AddressForm
                    name="template"
                    value={manifest.address as any}
                    shipment={manifest as any}
                    onSubmit={async (data) => {
                      dispatch({ name: "address", value: data });
                    }}
                  />
                </div>
              </CollapsibleContent>
            </Collapsible>

            {/* Reference section */}
            <InputField
              label="reference"
              name="reference"
              onChange={handleChange}
              defaultValue={manifest?.reference || ""}
              className="is-small"
              wrapperClass="column px-0 py-2"
              fieldClass="mb-0 p-0"
            />

            {created?.id && (
              <div className="notification is-success is-light p-3 my-3">
                <p className="has-text-weight-semibold mb-2">
                  SCAN form created.
                </p>
                <a
                  className={
                    "button is-small is-success is-light" +
                    (documentPrinter.isLoading ? " is-loading" : "")
                  }
                  onClick={(ev) => {
                    ev.preventDefault();
                    if (created?.id) documentPrinter.openManifest(created.id);
                  }}
                >
                  <span>Print SCAN form</span>
                </a>
              </div>
            )}

            <div className="p-3 my-5"></div>

            <div className="form-floating-footer has-text-centered p-1">
              <button
                className="button is-default m-1 is-small"
                type="button"
                onClick={close}
              >
                <span>Cancel</span>
              </button>
              <button
                className={
                  "button is-primary m-1 is-small" +
                  (mutation.createManifest.isLoading ? " is-loading" : "")
                }
                disabled={mutation.createManifest.isLoading}
                onClick={handleSubmit}
                type="button"
              >
                <span>
                  Create manifest{manifest.shipment_ids.length > 0 ? "s" : ""}
                </span>
              </button>
            </div>
          </>
        )}
      </div>
    );
  };

  return React.cloneElement(trigger, {
    onClick: () => modal.open(<ManifestFormComponent {...args} />),
  });
};
