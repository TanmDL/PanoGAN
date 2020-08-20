import torch
from .base_model import BaseModel
from . import networks

class panoganBaseline4aModel(BaseModel):
    """
        Xseq + Adversarial Feedback Loop
    """
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new dataset-specific options, and rewrite default values for existing options.
        Parameters:
            parser          -- original option parser
            is_train (bool) -- whether training phase or test phase. You can use this flag to add training-specific or test-specific options.
        Returns:
            the modified parser.
        """
        # changing the default values to match the pix2pix paper (https://phillipi.github.io/pix2pix/)
        parser.set_defaults(norm='instance', netG='unet_256', dataset_mode='aligned4')
        if is_train:
            parser.set_defaults(pool_size=0, gan_mode='vanilla')
            parser.add_argument('--lambda_L1', type=float, default=100.0, help='weight for L1 loss')
            parser.add_argument('--lambda_L1_seg', type=float, default=100.0, help='weight for L1 loss seg')
            parser.add_argument('--alpha', nargs='+', type=float,
                                help='alpha parameter in feedback loop')
            parser.add_argument('--loop_count', type=int, default=2, help='# feedback loop')
            parser.add_argument('--epoch_count_afl', type=int, default=20, help='# feedback loop')
            parser.add_argument('--afl_count', type=int, default=10, help='# feedback loop')
        return parser

    def __init__(self, opt):
        """Initialize the pix2pix class.
        Parameters:
            opt (Option class)-- stores all the experiment flags; needs to be a subclass of BaseOptions
        """
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        self.loss_names = ['G_GAN', 'G_L1', 'D1_real', 'D1_fake', 'D2_real', 'D2_fake']
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        if self.isTrain:
            self.visual_names = ['img_A', 'img_B', 'fake_B', 'img_D', 'fake_D']
        else:  # during test time, only load G
            self.visual_names = ['fake_B']
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>
        self.model_names = ['G', 'D_img', 'D_seg']
        # define networks (both generator and discriminator)
        self.netD_img = networks.define_D(2 * opt.input_nc, opt.ndf, opt.netD,
                                       opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)

        self.netD_seg = networks.define_D(2 * opt.input_nc, opt.ndf, opt.netD,
                                       opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)
        
        self.netG = networks.define_G(opt.input_nc, opt.output_nc*2, opt.ngf, opt.netG, opt.norm,
                                       not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids, self.netD_img,
                                       opt.alpha, opt.loop_count, opt.ndf)

        if self.isTrain:
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)
            self.criterionL1 = torch.nn.L1Loss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G_main = torch.optim.Adam(self.netG.module.main.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_G_netGA = torch.optim.Adam(self.netG.module.netGA.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D_img = torch.optim.Adam(self.netD_img.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D_seg = torch.optim.Adam(self.netD_seg.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G_main)
            self.optimizers.append(self.optimizer_G_netGA)
            self.optimizers.append(self.optimizer_D_img)
            self.optimizers.append(self.optimizer_D_seg)

    def set_input(self, input):
        AtoB = self.opt.direction == 'AtoB'
        self.img_A = input['A' if AtoB else 'B'].to(self.device)
        self.img_B = input['B' if AtoB else 'A'].to(self.device)
        self.img_D = input['D'].to(self.device)
        self.image_paths = input['A_paths' if AtoB else 'B_paths']
        self.real_label = 0.9
        self.false_label = 0.0

    def set_loop_count(self, loop_count):
        self.netG.module.loop_count = loop_count

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>."""
        self.fake_B, self.fake_D = self.netG(self.img_A)

    def backward_D(self):
        """Calculate GAN loss for the discriminator"""
        # Fake; stop backprop to the generator by detaching fake_B
        fake_AB = torch.cat((self.img_A, self.fake_B),
                           1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AB = self.netD_img(fake_AB.detach())  # Fake; stop backprop to the generator by detaching fake_B
        self.loss_D1_fake = self.criterionGAN(pred_fake_AB, self.false_label)
        # Real
        real_AB = torch.cat((self.img_A, self.img_B), 1)
        pred_real_AB = self.netD_img(real_AB)
        self.loss_D1_real = self.criterionGAN(pred_real_AB, self.real_label)

        fake_AD = torch.cat((self.img_A, self.fake_D),
                            1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AD = self.netD_seg(fake_AD.detach())  # Fake; stop backprop to the generator by detaching fake_B
        self.loss_D2_fake = self.criterionGAN(pred_fake_AD, self.false_label)
        # Real
        real_BD = torch.cat((self.img_A, self.img_D), 1)
        pred_real_AD = self.netD_seg(real_BD)
        self.loss_D2_real = self.criterionGAN(pred_real_AD, self.real_label)

        # combine loss and calculate gradients
        self.loss_D = (self.loss_D1_fake + self.loss_D1_real + self.loss_D2_fake + self.loss_D2_real) * 0.5
        self.loss_D.backward()


    def backward_G(self):
        """Calculate GAN and L1 loss for the generator"""
        # GAN loss
        fake_AB = torch.cat((self.img_A, self.fake_B), 1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AB = self.netD_img(fake_AB)
        self.loss_GAN_img = self.criterionGAN(pred_fake_AB, self.real_label)

        fake_AD = torch.cat((self.fake_B, self.fake_D), 1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AD = self.netD_seg(fake_AD)  # Fake; stop backprop to the generator by detaching fake_B
        self.loss_GAN_seg = self.criterionGAN(pred_fake_AD, self.real_label)

        self.loss_G_GAN = self.loss_GAN_img + self.loss_GAN_seg

        # L1 loss
        self.loss_L1_img = self.criterionL1(self.fake_B, self.img_B) * self.opt.lambda_L1
        self.loss_L1_seg = self.criterionL1(self.fake_D, self.img_D) * self.opt.lambda_L1_seg

        self.loss_G_L1 = self.loss_L1_img + self.loss_L1_seg
        self.loss_G = self.loss_G_GAN + self.loss_G_L1

        self.loss_G.backward()

    def optimize_parameters(self):
        self.forward()  # compute fake images: G(A)
        # update D
        self.set_requires_grad(self.netD_img, True)  # enable backprop for D
        self.set_requires_grad(self.netD_seg, True)  # enable backprop for D
        self.optimizer_D_img.zero_grad()  # set D's gradients to zero
        self.optimizer_D_seg.zero_grad()  # set D's gradients to zero
        self.backward_D()  # calculate gradients for D
        self.optimizer_D_img.step()  # update D's weights
        self.optimizer_D_seg.step()  # update D's weights

        # update G
        self.set_requires_grad(self.netD_img, False)  # D requires no gradients when optimizing G
        self.set_requires_grad(self.netD_seg, False)  # D requires no gradients when optimizing G
        self.set_requires_grad(self.netG.module.netGA, False) #AFL is not trained in the step 1
        self.set_requires_grad(self.netG.module.main, True)
        self.optimizer_G_main.zero_grad()  # set G's gradients to zero
        self.backward_G()  # calculate graidents for G
        self.optimizer_G_main.step()  # udpate G's weights

    def optimize_parameters_afl(self):
        self.forward()  # compute fake images: G(A)
        # update D
        self.set_requires_grad(self.netD_img, True)  # enable backprop for D
        feedback_seg = False
        self.set_requires_grad(self.netD_seg, feedback_seg)  # enable backprop for D
        self.optimizer_D_img.zero_grad()  # set D's gradients to zero
        if feedback_seg:
            self.optimizer_D_seg.zero_grad()  # set D's gradients to zero
        self.backward_D()  # calculate gradients for D
        self.optimizer_D_img.step()  # update D's weights
        if feedback_seg:
            self.optimizer_D_seg.step()  # update D's weights
        # update G
        self.set_requires_grad(self.netD_img, False)  # D requires no gradients when optimizing G
        self.set_requires_grad(self.netD_seg, False)  # D requires no gradients when optimizing G
        self.set_requires_grad(self.netG.module.main, False)  # AFL is not trained in the step 1
        self.set_requires_grad(self.netG.module.netGA, True)

        self.optimizer_G_netGA.zero_grad()  # set G's gradients to zero
        #self.optimizer_G2_netGA.zero_grad()  # set G's gradients to zero
        self.backward_G()  # calculate graidents for G
        self.optimizer_G_netGA.step()  # udpate G's weights
        #self.optimizer_G2.step()  # udpate G's weights